import json

from sqlalchemy import select

from api.generate import get_generation_gateway
from db.models_core import User
from db.models_usage import GenerationRun, UsageLog
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
    usage = (await async_db_session.execute(select(UsageLog))).scalar_one()
    assert usage.status == "completed"
    assert usage.run_id == run.id
    assert usage.credits == 1


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
