"""Naturalization review service tests."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from api.naturalization import get_assisted_naturalization_gateway
from db.models_codex import CodexAlias, CodexEntry
from db.models_core import ChapterBody, Project, User
from db.models_usage import GenerationRun, StyleProfile, UsageLog
from main import app
from services.generation import GenerationRoute
from services.naturalization import (
    AssistedNaturalizationGateway,
    AssistedNaturalizationPackage,
    AssistedNaturalizationResult,
    NaturalizationProviderError,
    NaturalizationResponseError,
    NaturalizationStaleError,
    accept_finding,
    scan_naturalization,
)


def _body_json(text: str) -> dict:
    return {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "attrs": {"pid": "p-1"},
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


class FakeAssistedGateway:
    def __init__(self, candidate: str | None = None, error: Exception | None = None):
        self.candidate = candidate
        self.error = error
        self.packages = []

    async def suggest(self, package):
        self.packages.append(package)
        if self.error is not None:
            raise self.error
        return AssistedNaturalizationResult(
            candidates={
                finding_id: (self.candidate or "沈禾今天揣着三两银子进城。", "删去模板化引导语")
                for finding_id in package.expected_finding_ids
            },
            prompt_tokens=121,
            cached_tokens=11,
            completion_tokens=31,
        )


async def _add_body(async_db_session, chapter_id: str, text: str, *, rev: int = 1) -> ChapterBody:
    body = ChapterBody(
        chapter_id=chapter_id,
        content_html=f'<p data-paragraph-id="p-1">{text}</p>',
        content_json=_body_json(text),
        rev=rev,
    )
    async_db_session.add(body)
    await async_db_session.flush()
    return body


@pytest.mark.asyncio
async def test_scan_creates_explainable_finding(seed_project, async_db_session):
    await seed_project(chapter_ids=("ch-natural",))
    async_db_session.add(
        ChapterBody(
            chapter_id="ch-natural",
            content_html="<p>值得注意的是，她缓缓走进门，仿佛已经等了很久。</p>",
            content_json=_body_json("值得注意的是，她缓缓走进门，仿佛已经等了很久。"),
            rev=3,
        )
    )
    await async_db_session.flush()

    run = await scan_naturalization(
        async_db_session,
        user_id="user_a",
        project_id="proj_a",
        chapter_id="ch-natural",
    )
    assert run.source_body_rev == 3
    assert run.finding_count == 1
    # The run and finding are separate tables; verify the candidate through a query
    # to keep the test independent of ORM relationship configuration.
    from db.models_naturalization import NaturalizationFinding

    finding = await async_db_session.scalar(select(NaturalizationFinding).where(NaturalizationFinding.run_id == run.id))
    assert finding is not None
    assert finding.paragraph_id == "p-1"
    assert finding.candidate_text != finding.original_text
    assert "template.transition" in finding.rule_ids


@pytest.mark.asyncio
async def test_accept_replaces_range_and_preserves_pid(seed_project, async_db_session, monkeypatch):
    await seed_project(chapter_ids=("ch-accept",))
    original = "值得注意的是，她缓缓走进门，仿佛已经等了很久。"
    async_db_session.add(
        ChapterBody(
            chapter_id="ch-accept",
            content_html=f"<p>{original}</p>",
            content_json=_body_json(original),
            rev=1,
        )
    )
    await async_db_session.flush()
    monkeypatch.setattr("services.naturalization.OutboxService.enqueue", AsyncMock())
    run = await scan_naturalization(
        async_db_session, user_id="user_a", project_id="proj_a", chapter_id="ch-accept"
    )
    from db.models_naturalization import NaturalizationFinding

    finding = await async_db_session.scalar(select(NaturalizationFinding).where(NaturalizationFinding.run_id == run.id))
    assert finding is not None
    accepted, _, new_rev = await accept_finding(async_db_session, finding_id=finding.id, project_id="proj_a")
    await async_db_session.flush()
    body = await async_db_session.get(ChapterBody, "ch-accept")
    assert accepted.status == "accepted"
    assert new_rev == 2
    assert body is not None and body.rev == 2
    paragraph = body.content_json["content"][0]
    assert paragraph["attrs"]["pid"] == "p-1"
    text = paragraph["content"][0]["text"]
    assert text == finding.candidate_text


@pytest.mark.asyncio
async def test_accept_replaces_only_the_matching_html_paragraph(
    seed_project, async_db_session, monkeypatch
):
    await seed_project(chapter_ids=("ch-duplicate",))
    original = "值得注意的是，她缓缓走进门，仿佛已经等了很久。"
    content_json = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "attrs": {"pid": paragraph_id},
                "content": [{"type": "text", "text": original}],
            }
            for paragraph_id in ("p-1", "p-2")
        ],
    }
    async_db_session.add(
        ChapterBody(
            chapter_id="ch-duplicate",
            content_html=(
                f'<p data-paragraph-id="p-1">{original}</p>'
                f'<p data-paragraph-id="p-2">{original}</p>'
            ),
            content_json=content_json,
            rev=1,
        )
    )
    await async_db_session.flush()
    monkeypatch.setattr("services.naturalization.OutboxService.enqueue", AsyncMock())
    run = await scan_naturalization(
        async_db_session,
        user_id="user_a",
        project_id="proj_a",
        chapter_id="ch-duplicate",
    )
    from db.models_naturalization import NaturalizationFinding

    findings = list(
        (
            await async_db_session.execute(
                select(NaturalizationFinding).where(NaturalizationFinding.run_id == run.id)
            )
        ).scalars()
    )
    finding = next(item for item in findings if item.paragraph_id == "p-2")

    await accept_finding(async_db_session, finding_id=finding.id, project_id="proj_a")
    body = await async_db_session.get(ChapterBody, "ch-duplicate")

    assert body is not None
    assert f'<p data-paragraph-id="p-1">{original}</p>' in body.content_html
    assert f'<p data-paragraph-id="p-2">{finding.candidate_text}</p>' in body.content_html


@pytest.mark.asyncio
async def test_accept_rejects_stale_body(seed_project, async_db_session, monkeypatch):
    await seed_project(chapter_ids=("ch-stale",))
    original = "值得注意的是，她缓缓走进门，仿佛已经等了很久。"
    body = ChapterBody(
        chapter_id="ch-stale", content_html=f"<p>{original}</p>", content_json=_body_json(original), rev=1
    )
    async_db_session.add(body)
    await async_db_session.flush()
    run = await scan_naturalization(
        async_db_session, user_id="user_a", project_id="proj_a", chapter_id="ch-stale"
    )
    from db.models_naturalization import NaturalizationFinding

    finding = await async_db_session.scalar(select(NaturalizationFinding).where(NaturalizationFinding.run_id == run.id))
    assert finding is not None
    body.content_json = _body_json("正文已经由作者改过。")
    with pytest.raises(NaturalizationStaleError):
        await accept_finding(async_db_session, finding_id=finding.id, project_id="proj_a")
    assert finding.status == "stale"


@pytest.mark.asyncio
async def test_assisted_scan_uses_style_profile_records_usage_and_never_writes_body(
    app_client, async_db_session, seed_project, auth_headers
):
    await seed_project(user_id="writer", project_id="novel", chapter_ids=("chapter",))
    profile = StyleProfile(
        id="style-natural",
        user_id="writer",
        name="克制白描",
        sample_text="这段私密样文绝不能进入自然化提示词。" * 500,
        sample_words=5_000,
        is_default=True,
        dimensions={"sentence_rhythm": {"summary": "短句为主，动作落点明确"}},
        alignment=0,
        status="ready",
    )
    async_db_session.add(profile)
    await async_db_session.flush()
    novel = await async_db_session.get(Project, "novel")
    novel.style_profile_id = profile.id
    original = "值得注意的是，沈禾今天带着三两银子缓缓进城。"
    await _add_body(async_db_session, "chapter", original)
    await async_db_session.commit()

    fake = FakeAssistedGateway("沈禾今天揣着三两银子进城。")
    app.dependency_overrides[get_assisted_naturalization_gateway] = lambda: fake
    try:
        response = await app_client.post(
            "/projects/novel/naturalization/scans",
            headers=auth_headers("writer"),
            json={"chapter_id": "chapter", "mode": "assisted"},
        )
    finally:
        app.dependency_overrides.pop(get_assisted_naturalization_gateway, None)

    assert response.status_code == 201
    payload = response.json()
    assert payload["mode"] == "assisted"
    assert payload["status"] == "ready"
    assert payload["style_profile_id"] == profile.id
    assert payload["model"] is not None
    assert payload["findings"][0]["candidate_text"] == "沈禾今天揣着三两银子进城。"
    assert payload["findings"][0]["validation"]["status"] == "passed"
    assert payload["findings"][0]["validation"]["candidate_source"] == "assisted"

    package = fake.packages[0]
    assert profile.sample_text not in json.dumps(package.messages, ensure_ascii=False)
    assert "短句为主" in package.messages[1]["content"]
    assert original not in package.messages[0]["content"]
    assert "prompt-security-v1" in package.messages[0]["content"]
    assert '<untrusted_data source="naturalization_items"' in package.messages[1]["content"]

    stored_body = await async_db_session.get(ChapterBody, "chapter")
    assert stored_body.rev == 1
    assert stored_body.content_json == _body_json(original)
    generation = (
        await async_db_session.execute(
            select(GenerationRun).where(GenerationRun.task_type == "naturalization_candidate")
        )
    ).scalar_one()
    assert generation.accepted_words == 0
    usage = (
        await async_db_session.execute(
            select(UsageLog).where(UsageLog.feature == "naturalization_assisted")
        )
    ).scalar_one()
    assert usage.status == "completed"
    assert usage.run_id == generation.id


@pytest.mark.asyncio
async def test_assisted_scan_uses_active_byok_without_platform_credit_charge(
    app_client, async_db_session, seed_project, auth_headers
):
    await seed_project(user_id="byok", project_id="novel", chapter_ids=("chapter",))
    user = await async_db_session.get(User, "byok")
    user.quota_remaining = 0
    user.quota_total = 0
    original = "值得注意的是，她缓缓推开院门。"
    await _add_body(async_db_session, "chapter", original)
    await async_db_session.commit()
    configured = await app_client.put(
        "/account/model-config",
        headers=auth_headers("byok"),
        json={
            "providerName": "作者模型",
            "baseUrl": "https://gateway.example.com/v1",
            "model": "author-model",
            "apiKey": "sk-author-owned-secret",
            "enabled": True,
            "revision": 0,
        },
    )
    assert configured.status_code == 200

    fake = FakeAssistedGateway("她推开院门。")
    app.dependency_overrides[get_assisted_naturalization_gateway] = lambda: fake
    try:
        response = await app_client.post(
            "/projects/novel/naturalization/scans",
            headers=auth_headers("byok"),
            json={"chapter_id": "chapter", "mode": "assisted"},
        )
    finally:
        app.dependency_overrides.pop(get_assisted_naturalization_gateway, None)

    assert response.status_code == 201
    assert fake.packages[0].route.source == "user"
    assert fake.packages[0].route.model_id == "author-model"
    assert fake.packages[0].route.api_key == "sk-author-owned-secret"
    usage = (
        await async_db_session.execute(
            select(UsageLog).where(UsageLog.feature == "naturalization_assisted")
        )
    ).scalar_one()
    assert usage.credits == 0
    assert usage.detail["billing_mode"] == "user_key"
    await async_db_session.refresh(user)
    assert user.quota_remaining == 0


@pytest.mark.asyncio
async def test_assisted_fact_lock_blocks_number_entity_and_time_changes(
    app_client, async_db_session, seed_project, auth_headers
):
    await seed_project(user_id="writer", project_id="novel", chapter_ids=("chapter",))
    async_db_session.add_all(
        [
            CodexEntry(
                id="cx-shen",
                project_id="novel",
                kind="character",
                name="沈禾",
                description="主角",
                attrs={},
                resident=True,
                status="confirmed",
                ref_chapters=[],
                conflicts=[],
            ),
            CodexEntry(
                id="cx-zhou",
                project_id="novel",
                kind="character",
                name="周掌柜",
                description="粮铺掌柜",
                attrs={},
                resident=True,
                status="confirmed",
                ref_chapters=[],
                conflicts=[],
            ),
        ]
    )
    await async_db_session.flush()
    async_db_session.add(CodexAlias(entry_id="cx-shen", alias="小禾"))
    original = "值得注意的是，沈禾今天带着三两银子缓缓进城。"
    await _add_body(async_db_session, "chapter", original)
    await async_db_session.commit()

    fake = FakeAssistedGateway("周掌柜昨天带着四两银子进城。")
    app.dependency_overrides[get_assisted_naturalization_gateway] = lambda: fake
    try:
        response = await app_client.post(
            "/projects/novel/naturalization/scans",
            headers=auth_headers("writer"),
            json={"chapter_id": "chapter", "mode": "assisted"},
        )
    finally:
        app.dependency_overrides.pop(get_assisted_naturalization_gateway, None)

    assert response.status_code == 201
    payload = response.json()
    finding = payload["findings"][0]
    assert payload["error_code"] == "ASSISTED_FACT_LOCK_BLOCKED"
    assert finding["validation"]["status"] == "blocked"
    assert finding["validation"]["numeric_facts_preserved"] is False
    assert finding["validation"]["temporal_facts_preserved"] is False
    assert finding["validation"]["entity_facts_preserved"] is False

    rejected = await app_client.post(
        f'/naturalization/runs/{payload["id"]}/findings/{finding["id"]}/accept',
        headers=auth_headers("writer"),
    )
    assert rejected.status_code == 422
    body = await async_db_session.get(ChapterBody, "chapter")
    assert body.rev == 1
    assert body.content_json == _body_json(original)


@pytest.mark.asyncio
async def test_assisted_provider_failure_falls_back_and_releases_reservation(
    app_client, async_db_session, seed_project, auth_headers
):
    await seed_project(user_id="writer", project_id="novel", chapter_ids=("chapter",))
    original = "值得注意的是，她缓缓推开院门。"
    await _add_body(async_db_session, "chapter", original)
    await async_db_session.commit()
    user = await async_db_session.get(User, "writer")
    before = user.quota_remaining
    fake = FakeAssistedGateway(error=NaturalizationProviderError("upstream unavailable"))
    app.dependency_overrides[get_assisted_naturalization_gateway] = lambda: fake
    try:
        response = await app_client.post(
            "/projects/novel/naturalization/scans",
            headers=auth_headers("writer"),
            json={"chapter_id": "chapter", "mode": "assisted"},
        )
    finally:
        app.dependency_overrides.pop(get_assisted_naturalization_gateway, None)

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["error_code"] == "NATURALIZATION_PROVIDER_ERROR"
    assert payload["findings"][0]["validation"]["assisted_status"] == "fallback"
    assert payload["findings"][0]["validation"]["candidate_source"] == "rules"
    usage = (
        await async_db_session.execute(
            select(UsageLog).where(UsageLog.feature == "naturalization_assisted")
        )
    ).scalar_one()
    assert usage.status == "released"
    await async_db_session.refresh(user)
    assert user.quota_remaining == before
    body = await async_db_session.get(ChapterBody, "chapter")
    assert body.rev == 1


@pytest.mark.asyncio
async def test_assisted_gateway_rejects_non_exact_structured_response():
    response = httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "candidates": [
                                    {
                                        "finding_id": "nf-1",
                                        "candidate_text": "候选",
                                        "reason": "原因",
                                        "unexpected": "field",
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        },
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response))
    gateway = AssistedNaturalizationGateway(client)
    package = AssistedNaturalizationPackage(
        run_id="nr-1",
        messages=[{"role": "system", "content": "system"}, {"role": "user", "content": "data"}],
        route=GenerationRoute(
            source="platform",
            endpoint="https://gateway.example/v1/chat/completions",
            api_key="secret",
            model_id="model",
            model_tier="main",
        ),
        expected_finding_ids=("nf-1",),
        target_words=200,
    )
    try:
        with pytest.raises(NaturalizationResponseError):
            await gateway.suggest(package)
    finally:
        await client.aclose()
