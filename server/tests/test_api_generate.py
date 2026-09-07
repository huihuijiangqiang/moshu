import json

from sqlalchemy import select

from api.generate import get_generation_gateway
from db.models_core import User
from db.models_usage import GenerationDraft, GenerationRun, UsageLog
from main import app
from services.generation import GenerationProviderError, StreamEvent


class FakeGenerationGateway:
    def __init__(self):
        self.packages = []

    async def stream(self, package):
        self.packages.append(package)
        yield StreamEvent("chunk", text="沈禾推开粮铺的门。")
        yield StreamEvent("chunk", text="周掌柜抬眼看她。")
        yield StreamEvent(
            "usage",
            usage={
                "prompt_tokens": 123,
                "completion_tokens": 18,
                "prompt_tokens_details": {"cached_tokens": 10},
            },
        )


class FailingGenerationGateway:
    async def stream(self, package):
        raise GenerationProviderError("upstream failed")
        yield


class PartiallyFailingGenerationGateway:
    async def stream(self, package):
        yield StreamEvent("chunk", text="她刚把第一畦菜种下，雨便落了下来。")
        raise GenerationProviderError("upstream disconnected")


class EchoingCredentialGateway:
    async def stream(self, package):
        raise GenerationProviderError(f"provider reflected credential {package.route.api_key}")
        yield


def parse_sse(text: str) -> list[object]:
    events = []
    for frame in text.split("\n\n"):
        if not frame.startswith("data: "):
            continue
        data = frame[6:]
        events.append(data if data == "[DONE]" else json.loads(data))
    return events


async def test_chapter_generation_streams_meta_text_done_and_records_run(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    chapters = await seed_project(
        user_id="writer",
        project_id="novel",
        chapter_ids=("ch1",),
        genre="女频 · 穿越种田",
    )
    chapters[0].outline = ["沈禾去粮铺谈青谷的收购价"]
    await async_db_session.flush()
    fake = FakeGenerationGateway()
    app.dependency_overrides[get_generation_gateway] = lambda: fake
    try:
        response = await app_client.post(
            "/generate/chapter",
            headers=auth_headers("writer"),
            json={
                "chapterId": "ch1",
                "targetWords": 1200,
                "model": "basic",
                "useStyleProfile": True,
                "dialogueDensity": "high",
            },
        )
    finally:
        app.dependency_overrides.pop(get_generation_gateway, None)

    assert response.status_code == 200
    events = parse_sse(response.text)
    assert events[0]["type"] == "meta"
    assert "genre.farming" in events[0]["skills"]
    assert "".join(event.get("text", "") for event in events if isinstance(event, dict)) == (
        "沈禾推开粮铺的门。周掌柜抬眼看她。"
    )
    assert events[-2]["type"] == "done"
    assert events[-2]["usage"]["credits"] == 1
    assert events[-1] == "[DONE]"

    run = (await async_db_session.execute(select(GenerationRun))).scalar_one()
    assert run.task_type == "chapter"
    assert run.prompt_tokens == 123
    assert run.cached_tokens == 10
    assert "task.chapter" in run.layer_report["skills"]
    assert run.layer_report["provenance"]["algorithm"] == "djb2-32-v1"
    assert len(run.layer_report["provenance"]["paragraph_hashes"]) == 1
    usage = (await async_db_session.execute(select(UsageLog))).scalar_one()
    assert usage.status == "completed"
    assert usage.run_id == run.id
    assert usage.credits == 1


async def test_prompt_preview_returns_exact_package_without_charging(
    app_client, async_db_session, seed_project, auth_headers
):
    chapters = await seed_project(
        user_id="preview_writer",
        project_id="preview_novel",
        chapter_ids=("preview_ch",),
        genre="女频 · 穿越种田",
    )
    chapters[0].outline = ["沈禾去粮铺谈青谷的收购价"]
    await async_db_session.flush()

    response = await app_client.post(
        "/generate/preview",
        headers=auth_headers("preview_writer"),
        json={
            "chapterId": "preview_ch",
            "targetWords": 1200,
            "model": "basic",
            "useStyleProfile": False,
            "dialogueDensity": "high",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["chapterId"] == "preview_ch"
    assert payload["targetWords"] == 1200
    assert payload["tokenBudget"]["prompt"] > 0
    assert {skill["id"] for skill in payload["skills"]} >= {
        "base.novel.zh",
        "genre.farming",
        "task.chapter",
    }
    assert payload["messages"][0]["role"] == "system"
    assert "青谷" in payload["messages"][1]["content"]
    assert (await async_db_session.execute(select(UsageLog))).scalars().all() == []
    assert "sk-" not in response.text


async def test_prompt_preview_includes_confirmed_chapter_time_anchor(
    app_client, async_db_session, seed_project, auth_headers
):
    chapters = await seed_project(
        user_id="temporal_writer",
        project_id="temporal_novel",
        chapter_ids=("temporal_prev", "temporal_current"),
    )
    chapters[0].temporal_anchor = {"start": "2024-03-12", "end": "2024-03-12", "precision": "day"}
    chapters[1].temporal_anchor = {"start": "2024-03-13", "end": "2024-03-20", "precision": "day"}
    await async_db_session.commit()

    response = await app_client.post(
        "/generate/preview",
        headers=auth_headers("temporal_writer"),
        json={"chapterId": "temporal_current", "targetWords": 800},
    )

    assert response.status_code == 200
    prompt = response.json()["messages"][1]["content"]
    assert "2024-03-13" in prompt
    assert "2024-03-20" in prompt
    assert "不得让本章时间早于上一章已确认的结束时间" in prompt


async def test_prompt_preview_requires_generation_permission(
    app_client, async_db_session, seed_project, auth_headers
):
    await seed_project(user_id="preview_owner", project_id="preview_private", chapter_ids=("preview_private_ch",))
    async_db_session.add(
        User(
            id="preview_viewer",
            name="preview_viewer",
            email="preview_viewer@example.test",
            quota_remaining=100,
            quota_total=100,
        )
    )
    await async_db_session.flush()

    response = await app_client.post(
        "/generate/preview",
        headers=auth_headers("preview_viewer"),
        json={"chapterId": "preview_private_ch", "targetWords": 800},
    )

    assert response.status_code == 403


async def test_user_model_config_routes_preview_and_generation_without_platform_credits(
    app_client, async_db_session, seed_project, auth_headers
):
    await seed_project(user_id="byok_writer", project_id="byok_novel", chapter_ids=("byok_ch",))
    user = await async_db_session.get(User, "byok_writer")
    user.quota_remaining = 0
    user.quota_total = 0
    await async_db_session.commit()
    configured = await app_client.put(
        "/account/model-config",
        headers=auth_headers("byok_writer"),
        json={
            "providerName": "作者中转站",
            "baseUrl": "https://gateway.example.com/v1",
            "model": "author-novel-model",
            "apiKey": "sk-author-owned-secret",
            "enabled": True,
            "revision": 0,
        },
    )
    assert configured.status_code == 200

    preview = await app_client.post(
        "/generate/preview",
        headers=auth_headers("byok_writer"),
        json={"chapterId": "byok_ch", "targetWords": 800, "model": "advanced"},
    )
    assert preview.status_code == 200
    assert preview.json()["model"] == {"id": "author-novel-model", "tier": "custom"}
    assert preview.json()["provider"]["source"] == "user"
    assert "sk-author-owned-secret" not in preview.text

    fake = FakeGenerationGateway()
    app.dependency_overrides[get_generation_gateway] = lambda: fake
    try:
        generated = await app_client.post(
            "/generate/chapter",
            headers=auth_headers("byok_writer"),
            json={"chapterId": "byok_ch", "targetWords": 800, "model": "advanced"},
        )
    finally:
        app.dependency_overrides.pop(get_generation_gateway, None)
    assert generated.status_code == 200
    done = parse_sse(generated.text)[-2]
    assert done["usage"]["credits"] == 0
    assert fake.packages[0].route.endpoint == "https://gateway.example.com/v1/chat/completions"
    assert fake.packages[0].route.api_key == "sk-author-owned-secret"
    run = (await async_db_session.execute(select(GenerationRun))).scalar_one()
    assert run.model_tier == "custom"
    usage = (await async_db_session.execute(select(UsageLog))).scalar_one()
    assert usage.status == "completed"
    assert usage.credits == 0
    assert usage.detail["billing_mode"] == "user_key"
    await async_db_session.refresh(user)
    assert user.quota_remaining == 0

    disabled = await app_client.put(
        "/account/model-config",
        headers=auth_headers("byok_writer"),
        json={
            "providerName": "作者中转站",
            "baseUrl": "https://gateway.example.com/v1",
            "model": "author-novel-model",
            "enabled": False,
            "revision": configured.json()["revision"],
        },
    )
    assert disabled.status_code == 200
    platform_preview = await app_client.post(
        "/generate/preview",
        headers=auth_headers("byok_writer"),
        json={"chapterId": "byok_ch", "targetWords": 800},
    )
    assert platform_preview.json()["provider"]["source"] == "platform"
    insufficient = await app_client.post(
        "/generate/chapter",
        headers=auth_headers("byok_writer"),
        json={"chapterId": "byok_ch", "targetWords": 800},
    )
    assert insufficient.status_code == 402


async def test_user_model_provider_error_cannot_reflect_api_key(
    app_client, async_db_session, seed_project, auth_headers
):
    await seed_project(user_id="secret_writer", project_id="secret_novel", chapter_ids=("secret_ch",))
    await app_client.put(
        "/account/model-config",
        headers=auth_headers("secret_writer"),
        json={
            "providerName": "provider",
            "baseUrl": "https://gateway.example.com/v1",
            "model": "model",
            "apiKey": "sk-never-reflect-this",
            "revision": 0,
        },
    )
    app.dependency_overrides[get_generation_gateway] = lambda: EchoingCredentialGateway()
    try:
        response = await app_client.post(
            "/generate/chapter",
            headers=auth_headers("secret_writer"),
            json={"chapterId": "secret_ch", "targetWords": 800},
        )
    finally:
        app.dependency_overrides.pop(get_generation_gateway, None)
    assert response.status_code == 200
    assert "sk-never-reflect-this" not in response.text
    assert "[redacted]" in response.text


async def test_inline_generation_passes_selected_text_and_checks_access(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_project(user_id="writer", project_id="novel", chapter_ids=("ch1",))
    async_db_session.add(
        User(
            id="stranger",
            name="stranger",
            email="stranger@example.test",
            quota_remaining=100,
            quota_total=100,
        )
    )
    await async_db_session.flush()
    fake = FakeGenerationGateway()
    app.dependency_overrides[get_generation_gateway] = lambda: fake
    payload = {
        "chapterId": "ch1",
        "targetWords": 400,
        "model": "basic",
        "useStyleProfile": False,
        "dialogueDensity": "mid",
        "action": "润色",
        "selectedText": "她走进门。",
        "nearbyText": "天刚亮。她走进门。",
    }
    try:
        forbidden = await app_client.post("/generate/inline", headers=auth_headers("stranger"), json=payload)
        response = await app_client.post("/generate/inline", headers=auth_headers("writer"), json=payload)
    finally:
        app.dependency_overrides.pop(get_generation_gateway, None)

    assert forbidden.status_code == 403
    assert response.status_code == 200
    assert "她走进门" in fake.packages[0].messages[1]["content"]
    assert "task.polish" in fake.packages[0].skills.ids


async def test_generation_rejects_insufficient_credits_before_opening_stream(
    app_client, async_db_session, seed_project, auth_headers
):
    await seed_project(user_id="empty_user", project_id="empty_project", chapter_ids=("empty_ch",))
    user = await async_db_session.get(User, "empty_user")
    user.quota_remaining = 0
    user.quota_total = 0
    await async_db_session.commit()

    response = await app_client.post(
        "/generate/chapter",
        headers=auth_headers("empty_user"),
        json={"chapterId": "empty_ch", "targetWords": 1200, "model": "advanced"},
    )

    assert response.status_code == 402
    assert response.json()["detail"]["code"] == "INSUFFICIENT_CREDITS"
    assert (await async_db_session.execute(select(UsageLog))).scalars().all() == []


async def test_generation_provider_failure_releases_reserved_credits(
    app_client, async_db_session, seed_project, auth_headers
):
    await seed_project(user_id="refund_writer", project_id="refund_novel", chapter_ids=("refund_ch",))
    app.dependency_overrides[get_generation_gateway] = lambda: FailingGenerationGateway()
    try:
        response = await app_client.post(
            "/generate/chapter",
            headers=auth_headers("refund_writer"),
            json={"chapterId": "refund_ch", "targetWords": 1200, "model": "basic"},
        )
    finally:
        app.dependency_overrides.pop(get_generation_gateway, None)

    assert response.status_code == 200
    assert parse_sse(response.text)[-1]["code"] == "generation_provider_error"
    user = await async_db_session.get(User, "refund_writer")
    log = (await async_db_session.execute(select(UsageLog))).scalar_one()
    assert user.quota_remaining == 1000
    assert log.status == "released"


async def test_completed_generation_persists_candidate_and_supports_idempotent_accept(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_project(user_id="draft_writer", project_id="draft_novel", chapter_ids=("draft_ch",))
    fake = FakeGenerationGateway()
    app.dependency_overrides[get_generation_gateway] = lambda: fake
    try:
        generated = await app_client.post(
            "/generate/chapter",
            headers=auth_headers("draft_writer"),
            json={"chapterId": "draft_ch", "targetWords": 1200, "model": "basic"},
        )
    finally:
        app.dependency_overrides.pop(get_generation_gateway, None)

    done = parse_sse(generated.text)[-2]
    assert done["draftId"]
    draft = await async_db_session.get(GenerationDraft, done["draftId"])
    assert draft is not None
    assert draft.status == "ready"
    assert draft.run_id == done["runId"]
    assert draft.content_text == "沈禾推开粮铺的门。周掌柜抬眼看她。"

    listing = await app_client.get(
        "/generate/drafts", params={"chapterId": "draft_ch"}, headers=auth_headers("draft_writer")
    )
    assert listing.status_code == 200
    assert listing.json()["items"][0]["id"] == draft.id
    assert "content" not in listing.json()["items"][0]

    detail = await app_client.get(f"/generate/drafts/{draft.id}", headers=auth_headers("draft_writer"))
    assert detail.json()["content"] == draft.content_text

    accepted = await app_client.post(
        f"/generate/drafts/{draft.id}/accept", headers=auth_headers("draft_writer")
    )
    accepted_again = await app_client.post(
        f"/generate/drafts/{draft.id}/accept", headers=auth_headers("draft_writer")
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"
    assert accepted_again.status_code == 200
    assert accepted_again.json()["acceptedAt"] == accepted.json()["acceptedAt"]

    cannot_reject = await app_client.delete(
        f"/generate/drafts/{draft.id}", headers=auth_headers("draft_writer")
    )
    assert cannot_reject.status_code == 409
    assert (
        await app_client.get(
            "/generate/drafts", params={"chapterId": "draft_ch"}, headers=auth_headers("draft_writer")
        )
    ).json()["items"] == []


async def test_partial_provider_failure_keeps_recoverable_candidate(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_project(user_id="partial_writer", project_id="partial_novel", chapter_ids=("partial_ch",))
    app.dependency_overrides[get_generation_gateway] = lambda: PartiallyFailingGenerationGateway()
    try:
        response = await app_client.post(
            "/generate/chapter",
            headers=auth_headers("partial_writer"),
            json={"chapterId": "partial_ch", "targetWords": 1200, "model": "basic"},
        )
    finally:
        app.dependency_overrides.pop(get_generation_gateway, None)

    assert parse_sse(response.text)[-1]["code"] == "generation_provider_error"
    draft = (await async_db_session.execute(select(GenerationDraft))).scalar_one()
    assert draft.status == "failed"
    assert draft.run_id is not None
    assert draft.content_text == "她刚把第一畦菜种下，雨便落了下来。"
    assert draft.generated_words > 0
    run = await async_db_session.get(GenerationRun, draft.run_id)
    assert run is not None
    assert run.layer_report["incomplete"] is True
    assert run.layer_report["provenance"]["paragraph_hashes"]

    accepted = await app_client.post(
        f"/generate/drafts/{draft.id}/accept", headers=auth_headers("partial_writer")
    )
    assert accepted.status_code == 200
    assert accepted.json()["content"] == draft.content_text


async def test_draft_review_persists_paragraph_decisions_and_accepts_only_selected_text(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_project(user_id="review_writer", project_id="review_novel", chapter_ids=("review_ch",))
    draft = GenerationDraft(
        id="draft_review",
        user_id="review_writer",
        project_id="review_novel",
        chapter_id="review_ch",
        kind="chapter",
        status="ready",
        content_text="第一段。\n第二段。\n第三段。",
        generated_words=12,
        request_summary={},
    )
    async_db_session.add(draft)
    await async_db_session.commit()

    detail = await app_client.get("/generate/drafts/draft_review", headers=auth_headers("review_writer"))
    assert detail.status_code == 200
    assert detail.json()["review"] == {"total": 3, "pending": 3, "accepted": 0, "rejected": 0}
    assert [segment["id"] for segment in detail.json()["segments"]] == ["p1", "p2", "p3"]

    first = await app_client.patch(
        "/generate/drafts/draft_review/review",
        headers=auth_headers("review_writer"),
        json={"segmentIds": ["p1", "p3"], "decision": "accepted", "baseVersion": 0},
    )
    assert first.status_code == 200
    assert first.json()["reviewVersion"] == 1
    assert first.json()["review"] == {"total": 3, "pending": 1, "accepted": 2, "rejected": 0}

    stale = await app_client.patch(
        "/generate/drafts/draft_review/review",
        headers=auth_headers("review_writer"),
        json={"segmentIds": ["p2"], "decision": "rejected", "baseVersion": 0},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "DRAFT_REVIEW_CONFLICT"
    assert stale.json()["detail"]["currentVersion"] == 1

    incomplete = await app_client.post(
        "/generate/drafts/draft_review/accept", headers=auth_headers("review_writer")
    )
    assert incomplete.status_code == 409
    assert incomplete.json()["detail"]["code"] == "DRAFT_REVIEW_INCOMPLETE"

    final = await app_client.patch(
        "/generate/drafts/draft_review/review",
        headers=auth_headers("review_writer"),
        json={"segmentIds": ["p2"], "decision": "rejected", "baseVersion": 1},
    )
    assert final.status_code == 200
    assert final.json()["review"] == {"total": 3, "pending": 0, "accepted": 2, "rejected": 1}

    accepted = await app_client.post(
        "/generate/drafts/draft_review/accept", headers=auth_headers("review_writer")
    )
    assert accepted.status_code == 200
    assert accepted.json()["content"] == "第一段。\n第三段。"


async def test_draft_review_can_undo_a_decision_and_rejects_invalid_segments(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_project(user_id="undo_writer", project_id="undo_novel", chapter_ids=("undo_ch",))
    async_db_session.add(
        GenerationDraft(
            id="draft_undo",
            user_id="undo_writer",
            project_id="undo_novel",
            chapter_id="undo_ch",
            kind="inline",
            status="ready",
            content_text="保留。\n待定。",
            generated_words=6,
            request_summary={},
        )
    )
    await async_db_session.commit()

    invalid = await app_client.patch(
        "/generate/drafts/draft_undo/review",
        headers=auth_headers("undo_writer"),
        json={"segmentIds": ["p9"], "decision": "accepted", "baseVersion": 0},
    )
    assert invalid.status_code == 422

    decided = await app_client.patch(
        "/generate/drafts/draft_undo/review",
        headers=auth_headers("undo_writer"),
        json={"segmentIds": ["p1"], "decision": "accepted", "baseVersion": 0},
    )
    undone = await app_client.patch(
        "/generate/drafts/draft_undo/review",
        headers=auth_headers("undo_writer"),
        json={"segmentIds": ["p1"], "decision": "pending", "baseVersion": decided.json()["reviewVersion"]},
    )
    assert undone.status_code == 200
    assert undone.json()["review"] == {"total": 2, "pending": 2, "accepted": 0, "rejected": 0}


async def test_draft_access_requires_body_edit_permission_and_reject_is_idempotent(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_project(user_id="owner", project_id="private_novel", chapter_ids=("private_ch",))
    async_db_session.add(
        User(
            id="outsider",
            name="outsider",
            email="outsider@example.test",
            quota_remaining=100,
            quota_total=100,
        )
    )
    draft = GenerationDraft(
        id="draft_private",
        user_id="owner",
        project_id="private_novel",
        chapter_id="private_ch",
        kind="inline",
        status="ready",
        content_text="只属于这本书的候选。",
        generated_words=10,
        request_summary={"action": "续写"},
    )
    async_db_session.add(draft)
    await async_db_session.commit()

    forbidden = await app_client.get(
        "/generate/drafts", params={"chapterId": "private_ch"}, headers=auth_headers("outsider")
    )
    assert forbidden.status_code == 403

    rejected = await app_client.delete("/generate/drafts/draft_private", headers=auth_headers("owner"))
    rejected_again = await app_client.delete("/generate/drafts/draft_private", headers=auth_headers("owner"))
    assert rejected.status_code == 204
    assert rejected_again.status_code == 204
    await async_db_session.refresh(draft)
    assert draft.status == "rejected"
    assert draft.rejected_at is not None


async def test_streaming_draft_cannot_be_accepted_or_rejected(
    app_client,
    async_db_session,
    seed_project,
    auth_headers,
):
    await seed_project(user_id="stream_writer", project_id="stream_novel", chapter_ids=("stream_ch",))
    draft = GenerationDraft(
        id="draft_streaming",
        user_id="stream_writer",
        project_id="stream_novel",
        chapter_id="stream_ch",
        kind="chapter",
        status="streaming",
        content_text="仍在写入的内容",
        generated_words=7,
        request_summary={},
    )
    async_db_session.add(draft)
    await async_db_session.commit()

    accepted = await app_client.post(
        "/generate/drafts/draft_streaming/accept", headers=auth_headers("stream_writer")
    )
    rejected = await app_client.delete(
        "/generate/drafts/draft_streaming", headers=auth_headers("stream_writer")
    )
    assert accepted.status_code == 409
    assert rejected.status_code == 409
    await async_db_session.refresh(draft)
    assert draft.status == "streaming"
