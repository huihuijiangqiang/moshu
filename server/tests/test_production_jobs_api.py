from datetime import UTC, datetime, timedelta
from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import select

from celery_app import celery_app
from config import settings
from db import Adaptation, CodexEntry, Episode, ProductionAsset, ProductionJob, Scene, Shot, VisualProfile
from db.models_core import User
from db.models_usage import UsageLog
from providers.production_images import GeneratedImage, ImageGatewayError
from tasks import production


@pytest.fixture
def image_settings(monkeypatch):
    monkeypatch.setattr(settings, "image_gateway_url", "https://image.example/v1")
    monkeypatch.setattr(settings, "image_gateway_key", "test-key")
    monkeypatch.setattr(settings, "image_generation_credits", 23)
    dispatched = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, **kwargs: dispatched.append((name, kwargs)))
    return dispatched


async def seed_shot(db, seed_project, *, locked=True):
    await seed_project(user_id="image_owner", project_id="image_project")
    db.add_all([
        CodexEntry(
            id="image_character", project_id="image_project", kind="character", name="岚",
            description="", attrs={}, resident=False, status="confirmed", ref_chapters=[], conflicts=[],
        ),
        Adaptation(id="image_adaptation", project_id="image_project", title="星际第一季", aspect_ratio="9:16"),
        Episode(id="image_episode", adaptation_id="image_adaptation", number=1, title="第一集", source_chapter_ids=[]),
        Scene(
            id="image_scene", episode_id="image_episode", order=1, purpose="交战",
            character_entry_ids=["image_character"], summary="星际战场",
        ),
        Shot(
            id="image_shot", scene_id="image_scene", order=1, shot_type="close",
            action="女主启动机甲", visual_prompt="驾驶舱内的决断瞬间",
        ),
        VisualProfile(
            id="image_profile", adaptation_id="image_adaptation", codex_entry_id="image_character",
            display_name="岚", appearance="黑色短发，左眉有细疤", costume="灰白驾驶服", locked=locked,
        ),
    ])
    await db.commit()


@pytest.mark.asyncio
async def test_image_job_preview_confirm_idempotency_and_cancel(
    app_client, async_db_session, seed_project, auth_headers, image_settings,
):
    await seed_shot(async_db_session, seed_project)
    headers = auth_headers("image_owner")
    preview_response = await app_client.get("/shots/image_shot/image-preview", headers=headers)
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["ready"] is True
    assert preview["credits"] == 23
    assert "左眉有细疤" in preview["prompt"]
    assert preview["profile_versions"] == {"image_character": 1}

    payload = {"client_request_id": "image-request-001", "prompt_sha256": preview["prompt_sha256"], "model": preview["model"], "credits": preview["credits"]}
    created = await app_client.post("/shots/image_shot/image-jobs", headers=headers, json=payload)
    assert created.status_code == 201
    job_id = created.json()["id"]
    repeated = await app_client.post("/shots/image_shot/image-jobs", headers=headers, json=payload)
    assert repeated.status_code == 201
    assert repeated.json()["id"] == job_id
    assert len(image_settings) == 1
    assert image_settings[0][0] == "production.generate_image"
    assert (await async_db_session.get(User, "image_owner")).quota_remaining == 977
    logs = (await async_db_session.execute(select(UsageLog).where(UsageLog.feature == "comic_image"))).scalars().all()
    assert len(logs) == 1 and logs[0].status == "reserved"

    listed = await app_client.get("/shots/image_shot/image-jobs", headers=headers)
    assert [item["id"] for item in listed.json()] == [job_id]
    cancelled = await app_client.post(f"/image-jobs/{job_id}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert (await async_db_session.get(UsageLog, logs[0].id)).status == "released"
    await async_db_session.refresh(await async_db_session.get(User, "image_owner"))
    assert (await async_db_session.get(User, "image_owner")).quota_remaining == 1000
    assert (await app_client.post(f"/image-jobs/{job_id}/cancel", headers=headers)).status_code == 409


@pytest.mark.asyncio
async def test_full_body_character_sheet_preview_job_and_worker_asset(
    app_client, async_db_session, seed_project, auth_headers, image_settings, monkeypatch, tmp_path,
):
    await seed_shot(async_db_session, seed_project)
    monkeypatch.setattr(settings, "production_asset_dir", str(tmp_path))
    headers = auth_headers("image_owner")
    preview_response = await app_client.get("/visual-profiles/image_profile/full-body-preview", headers=headers)
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["ready"] is True
    assert "complete standing pose" in preview["prompt"]

    created = await app_client.post("/visual-profiles/image_profile/full-body-jobs", headers=headers, json={
        "client_request_id": "full-body-request-001", "prompt_sha256": preview["prompt_sha256"],
        "model": preview["model"], "credits": preview["credits"],
    })
    assert created.status_code == 201
    job_id = created.json()["id"]
    output = BytesIO()
    Image.new("RGB", (512, 1024), "#345b62").save(output, format="PNG")

    async def fake_generate(prompt, *, aspect_ratio, model):
        assert "complete standing pose" in prompt
        return GeneratedImage(output.getvalue(), "image/png", 512, 1024, "sheet-provider")

    monkeypatch.setattr(production, "generate_storyboard_image", fake_generate)
    assert await production.process_image_job(async_db_session, job_id) == "completed"
    job = await async_db_session.get(ProductionJob, job_id)
    asset = await async_db_session.get(ProductionAsset, job.asset_id)
    profile = await async_db_session.get(VisualProfile, "image_profile")
    assert job.visual_profile_id == profile.id
    assert asset.kind == "character_sheet" and asset.visual_profile_id == profile.id
    assert asset.id in profile.reference_asset_ids
    assert (await app_client.get(f"/assets/{asset.id}/content", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_image_job_rejects_stale_preview_and_unlocked_character(
    app_client, async_db_session, seed_project, auth_headers, image_settings,
):
    await seed_shot(async_db_session, seed_project)
    headers = auth_headers("image_owner")
    preview = (await app_client.get("/shots/image_shot/image-preview", headers=headers)).json()
    shot = await async_db_session.get(Shot, "image_shot")
    shot.visual_prompt = "改成另一个画面"
    await async_db_session.commit()
    response = await app_client.post("/shots/image_shot/image-jobs", headers=headers, json={
        "client_request_id": "image-request-002", "prompt_sha256": preview["prompt_sha256"], "model": preview["model"], "credits": preview["credits"],
    })
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "image_preview_changed"
    assert await async_db_session.scalar(select(ProductionJob.id)) is None

    profile = await async_db_session.get(VisualProfile, "image_profile")
    profile.locked = False
    await async_db_session.commit()
    preview = (await app_client.get("/shots/image_shot/image-preview", headers=headers)).json()
    assert preview["ready"] is False
    response = await app_client.post("/shots/image_shot/image-jobs", headers=headers, json={
        "client_request_id": "image-request-003", "prompt_sha256": preview["prompt_sha256"], "model": preview["model"], "credits": preview["credits"],
    })
    assert response.status_code == 422
    assert "visual_profile_not_ready:image_character" in response.json()["detail"]["issues"]


@pytest.mark.asyncio
async def test_image_job_checks_project_permission_and_price(
    app_client, async_db_session, seed_project, make_user, auth_headers, image_settings, monkeypatch,
):
    await seed_shot(async_db_session, seed_project)
    async_db_session.add(make_user("image_outsider"))
    await async_db_session.commit()
    assert (await app_client.get(
        "/shots/image_shot/image-preview", headers=auth_headers("image_outsider")
    )).status_code == 403
    monkeypatch.setattr(settings, "image_generation_credits", 0)
    preview = (await app_client.get(
        "/shots/image_shot/image-preview", headers=auth_headers("image_owner")
    )).json()
    assert preview["ready"] is False
    assert "image_price_not_configured" in preview["issues"]


@pytest.mark.asyncio
async def test_image_job_refuses_changed_price_after_preview(
    app_client, async_db_session, seed_project, auth_headers, image_settings, monkeypatch,
):
    await seed_shot(async_db_session, seed_project)
    headers = auth_headers("image_owner")
    preview = (await app_client.get("/shots/image_shot/image-preview", headers=headers)).json()
    monkeypatch.setattr(settings, "image_generation_credits", 42)
    response = await app_client.post("/shots/image_shot/image-jobs", headers=headers, json={
        "client_request_id": "image-price-changed", "prompt_sha256": preview["prompt_sha256"],
        "model": preview["model"], "credits": preview["credits"],
    })
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "image_preview_changed"
    assert (await async_db_session.get(User, "image_owner")).quota_remaining == 1000
    assert await async_db_session.scalar(select(ProductionJob.id)) is None


@pytest.mark.asyncio
async def test_image_worker_persists_private_draft_and_charges_once(
    app_client, async_db_session, seed_project, auth_headers, image_settings, monkeypatch, tmp_path,
):
    await seed_shot(async_db_session, seed_project)
    monkeypatch.setattr(settings, "production_asset_dir", str(tmp_path))
    output = BytesIO()
    Image.new("RGB", (64, 96), "#945b69").save(output, format="PNG")
    called = []

    async def fake_generate(prompt, *, aspect_ratio, model):
        called.append((prompt, aspect_ratio, model))
        return GeneratedImage(output.getvalue(), "image/png", 64, 96, "provider-result")

    monkeypatch.setattr(production, "generate_storyboard_image", fake_generate)
    headers = auth_headers("image_owner")
    preview = (await app_client.get("/shots/image_shot/image-preview", headers=headers)).json()
    created = await app_client.post("/shots/image_shot/image-jobs", headers=headers, json={
        "client_request_id": "image-worker-001", "prompt_sha256": preview["prompt_sha256"], "model": preview["model"], "credits": preview["credits"],
    })
    job_id = created.json()["id"]

    assert await production.process_image_job(async_db_session, job_id) == "completed"
    assert await production.process_image_job(async_db_session, job_id) == "not_queued"
    job = await async_db_session.get(ProductionJob, job_id)
    asset = await async_db_session.get(ProductionAsset, job.asset_id)
    log = await async_db_session.get(UsageLog, job.usage_log_id)
    assert job.status == "completed" and asset.status == "draft"
    assert asset.metadata_json == {"source": "image_generation", "job_id": job_id, "provider_id": "provider-result"}
    assert (tmp_path / asset.storage_key).read_bytes() == output.getvalue()
    assert log.status == "completed" and log.credits == 23
    assert (await async_db_session.get(User, "image_owner")).quota_remaining == 977
    assert asset.id in (await async_db_session.get(Shot, "image_shot")).reference_asset_ids
    assert len(called) == 1 and called[0][1:] == ("9:16", "gpt-image-2")
    assert (await app_client.get(f"/assets/{asset.id}/content", headers=headers)).status_code == 200
    package = (await app_client.get("/episodes/image_episode/production-package", headers=headers)).json()
    assert asset.id in [item["id"] for item in package["assets"]]
    assert any(item["code"] == "asset_unapproved" for item in package["readiness"]["issues"])
    assert (await app_client.post(f"/image-jobs/{job_id}/cancel", headers=headers)).status_code == 409


@pytest.mark.asyncio
async def test_image_worker_failure_releases_credits(
    app_client, async_db_session, seed_project, auth_headers, image_settings, monkeypatch,
):
    await seed_shot(async_db_session, seed_project)

    async def fail_generate(prompt, *, aspect_ratio, model):
        raise ImageGatewayError("image_provider_forbidden")

    monkeypatch.setattr(production, "generate_storyboard_image", fail_generate)
    headers = auth_headers("image_owner")
    preview = (await app_client.get("/shots/image_shot/image-preview", headers=headers)).json()
    created = await app_client.post("/shots/image_shot/image-jobs", headers=headers, json={
        "client_request_id": "image-worker-002", "prompt_sha256": preview["prompt_sha256"], "model": preview["model"], "credits": preview["credits"],
    })
    job_id = created.json()["id"]
    assert await production.process_image_job(async_db_session, job_id) == "failed"
    job = await async_db_session.get(ProductionJob, job_id)
    log = await async_db_session.get(UsageLog, job.usage_log_id)
    user = await async_db_session.get(User, "image_owner")
    assert job.status == "failed" and job.error_code == "image_provider_forbidden"
    assert log.status == "released" and user.quota_remaining == 1000
    assert await async_db_session.scalar(select(ProductionAsset.id)) is None


@pytest.mark.asyncio
async def test_stale_image_job_recovery_releases_credits(
    app_client, async_db_session, seed_project, auth_headers, image_settings,
):
    await seed_shot(async_db_session, seed_project)
    headers = auth_headers("image_owner")
    preview = (await app_client.get("/shots/image_shot/image-preview", headers=headers)).json()
    created = await app_client.post("/shots/image_shot/image-jobs", headers=headers, json={
        "client_request_id": "image-worker-003", "prompt_sha256": preview["prompt_sha256"], "model": preview["model"], "credits": preview["credits"],
    })
    job = await async_db_session.get(ProductionJob, created.json()["id"])
    now = datetime.now(UTC)
    job.status = "running"
    job.started_at = now - timedelta(minutes=30)
    await async_db_session.commit()

    assert await production.recover_stale_image_jobs(async_db_session, now=now) == 1
    assert await production.recover_stale_image_jobs(async_db_session, now=now) == 0
    assert job.status == "failed" and job.error_code == "image_job_timeout"
    assert (await async_db_session.get(UsageLog, job.usage_log_id)).status == "released"
    assert (await async_db_session.get(User, "image_owner")).quota_remaining == 1000


@pytest.mark.asyncio
async def test_expired_image_reservation_never_calls_provider(
    app_client, async_db_session, seed_project, auth_headers, image_settings, monkeypatch,
):
    await seed_shot(async_db_session, seed_project)
    headers = auth_headers("image_owner")
    preview = (await app_client.get("/shots/image_shot/image-preview", headers=headers)).json()
    created = await app_client.post("/shots/image_shot/image-jobs", headers=headers, json={
        "client_request_id": "image-worker-004", "prompt_sha256": preview["prompt_sha256"], "model": preview["model"], "credits": preview["credits"],
    })
    job = await async_db_session.get(ProductionJob, created.json()["id"])
    log = await async_db_session.get(UsageLog, job.usage_log_id)
    log.reservation_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await async_db_session.commit()

    async def forbidden_call(*args, **kwargs):
        raise AssertionError("expired reservation must not spend provider credits")

    monkeypatch.setattr(production, "generate_storyboard_image", forbidden_call)
    assert await production.process_image_job(async_db_session, job.id) == "reservation_expired"
    assert job.status == "failed" and job.error_code == "image_reservation_expired"
    assert log.status == "released"
    assert (await async_db_session.get(User, "image_owner")).quota_remaining == 1000
