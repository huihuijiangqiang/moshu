from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from db.models_usage import StyleProfile, UsageLog
from services.generation import GenerationService
from services.style_profiles import (
    StyleAnalysis,
    StyleExtractionError,
    StyleExtractionGateway,
    count_sample_words,
    sample_for_analysis,
)


def _sample(words: int = 5_100) -> str:
    return "春" * words


async def _seed_user(async_db_session, make_user, user_id: str) -> None:
    async_db_session.add(make_user(user_id, quota_remaining=10_000, quota_total=10_000))
    await async_db_session.commit()


@pytest.mark.asyncio
async def test_style_profiles_are_private_and_crud_is_real(
    app_client, async_db_session, auth_headers, make_user
):
    await _seed_user(async_db_session, make_user, "owner")
    await _seed_user(async_db_session, make_user, "other")

    created = await app_client.post(
        "/styles",
        headers=auth_headers("owner"),
        json={"name": "田园白描", "sample_text": _sample(), "is_default": False},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "pending"
    assert body["is_default"] is True
    assert body["sample_words"] >= 5_000
    assert "sample_text" not in body

    hidden = await app_client.get(f"/styles/{body['id']}", headers=auth_headers("other"))
    assert hidden.status_code == 404
    other_rows = await app_client.get("/styles", headers=auth_headers("other"))
    assert other_rows.json() == []

    updated = await app_client.patch(
        f"/styles/{body['id']}",
        headers=auth_headers("owner"),
        json={"name": "田园短句"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "田园短句"


@pytest.mark.asyncio
async def test_only_one_default_and_deleting_it_promotes_a_replacement(
    app_client, async_db_session, auth_headers, make_user
):
    await _seed_user(async_db_session, make_user, "owner")
    first = (
        await app_client.post(
            "/styles",
            headers=auth_headers("owner"),
            json={"name": "一号", "sample_text": _sample()},
        )
    ).json()
    second = (
        await app_client.post(
            "/styles",
            headers=auth_headers("owner"),
            json={"name": "二号", "sample_text": _sample(), "is_default": True},
        )
    ).json()
    rows = (await app_client.get("/styles", headers=auth_headers("owner"))).json()
    assert [row["id"] for row in rows if row["is_default"]] == [second["id"]]

    response = await app_client.delete(f"/styles/{second['id']}", headers=auth_headers("owner"))
    assert response.status_code == 204
    remaining = (await app_client.get("/styles", headers=auth_headers("owner"))).json()
    assert remaining[0]["id"] == first["id"]
    assert remaining[0]["is_default"] is True


@pytest.mark.asyncio
async def test_extract_validates_sample_and_records_completed_usage(
    app_client, async_db_session, auth_headers, make_user, monkeypatch
):
    await _seed_user(async_db_session, make_user, "owner")
    too_short = (
        await app_client.post(
            "/styles",
            headers=auth_headers("owner"),
            json={"name": "短样本", "sample_text": "只有几句话。"},
        )
    ).json()
    rejected = await app_client.post(
        f"/styles/{too_short['id']}/extract", headers=auth_headers("owner")
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "STYLE_SAMPLE_TOO_SHORT"

    profile = (
        await app_client.post(
            "/styles",
            headers=auth_headers("owner"),
            json={"name": "长样本", "sample_text": _sample()},
        )
    ).json()
    dimensions = {
        key: {"title": key, "score": 60, "summary": f"{key} 的统计说明", "traits": [], "avoid": []}
        for key in (
            "sentence_rhythm",
            "dialogue",
            "description_density",
            "imagery",
            "chapter_hooks",
            "recurring_language",
        )
    }

    async def fake_analyze(self, sample_text):
        return StyleAnalysis(dimensions, 120, 20, 80)

    monkeypatch.setattr(StyleExtractionGateway, "analyze", fake_analyze)
    response = await app_client.post(
        f"/styles/{profile['id']}/extract", headers=auth_headers("owner")
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["dimensions"] == dimensions
    log = await async_db_session.scalar(
        select(UsageLog).where(UsageLog.user_id == "owner", UsageLog.feature == "style_extract")
    )
    assert log is not None
    assert log.status == "completed"
    assert log.prompt_tokens == 120
    assert log.completion_tokens == 80


@pytest.mark.asyncio
async def test_extract_failure_is_visible_and_refunds_reservation(
    app_client, async_db_session, auth_headers, make_user, monkeypatch
):
    await _seed_user(async_db_session, make_user, "owner")
    profile = (
        await app_client.post(
            "/styles",
            headers=auth_headers("owner"),
            json={"name": "会失败", "sample_text": _sample()},
        )
    ).json()

    async def fail(self, sample_text):
        raise StyleExtractionError("返回格式错误")

    monkeypatch.setattr(StyleExtractionGateway, "analyze", fail)
    response = await app_client.post(
        f"/styles/{profile['id']}/extract", headers=auth_headers("owner")
    )
    assert response.status_code == 502
    stored = await async_db_session.get(StyleProfile, profile["id"])
    await async_db_session.refresh(stored)
    assert stored.status == "failed"
    assert "返回格式错误" in stored.error_detail
    log = await async_db_session.scalar(
        select(UsageLog).where(UsageLog.user_id == "owner", UsageLog.feature == "style_extract")
    )
    assert log.status == "released"


@pytest.mark.asyncio
async def test_only_owner_can_bind_ready_profile_and_delete_unbinds(
    app_client, async_db_session, auth_headers, make_user, make_project
):
    await _seed_user(async_db_session, make_user, "owner")
    await _seed_user(async_db_session, make_user, "other")
    project = make_project("project", owner_id="owner")
    profile = StyleProfile(
        id="style_ready",
        user_id="owner",
        name="已就绪",
        sample_text=_sample(),
        sample_words=5_100,
        is_default=True,
        dimensions={"sentence_rhythm": {"summary": "短句"}},
        alignment=0,
        status="ready",
        extracted_at=datetime.now(UTC),
    )
    async_db_session.add(profile)
    await async_db_session.flush()
    async_db_session.add(project)
    await async_db_session.commit()

    denied = await app_client.put(
        "/projects/project/style-profile",
        headers=auth_headers("other"),
        json={"style_profile_id": profile.id},
    )
    assert denied.status_code == 403
    bound = await app_client.put(
        "/projects/project/style-profile",
        headers=auth_headers("owner"),
        json={"style_profile_id": profile.id},
    )
    assert bound.status_code == 200
    assert bound.json()["style_profile_id"] == profile.id

    deleted = await app_client.delete(f"/styles/{profile.id}", headers=auth_headers("owner"))
    assert deleted.status_code == 204
    await async_db_session.refresh(project)
    assert project.style_profile_id is None


@pytest.mark.asyncio
async def test_generation_prompt_uses_ready_profile_and_never_sample_text(
    async_db_session, make_user, make_project
):
    async_db_session.add(make_user("owner"))
    await async_db_session.flush()
    profile = StyleProfile(
        id="style_ready",
        user_id="owner",
        name="克制短句",
        sample_text="这是一句绝不能进入提示词的私密样文。",
        sample_words=5_100,
        is_default=True,
        dimensions={"sentence_rhythm": {"summary": "短句占比高"}},
        alignment=0,
        status="ready",
    )
    project = make_project("project", owner_id="owner", style_profile_id=profile.id)
    async_db_session.add(profile)
    await async_db_session.flush()
    async_db_session.add(project)
    await async_db_session.flush()

    prompt = await GenerationService(async_db_session)._style_prompt(project, True)
    assert "克制短句" in prompt
    assert "短句占比高" in prompt
    assert profile.sample_text not in prompt


def test_style_sample_is_counted_and_evenly_sampled():
    text = "甲" * 60_000
    sampled = sample_for_analysis(text)
    assert len(sampled) <= 30_100
    assert count_sample_words("春风 A story 2026") == 5
