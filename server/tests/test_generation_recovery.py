from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from db.models_core import User
from db.models_usage import GenerationDraft, UsageLog
from tasks.generation import recover_stale_generation_state


@pytest.mark.asyncio
async def test_recover_stale_generation_stream_and_expired_reservation(
    async_db_session, seed_project
):
    await seed_project(
        user_id="recovery_writer",
        project_id="recovery_project",
        chapter_ids=("recovery_ch",),
    )
    now = datetime.now(UTC)
    async_db_session.add_all(
        [
            GenerationDraft(
                id="draft_stale_recovery",
                user_id="recovery_writer",
                project_id="recovery_project",
                chapter_id="recovery_ch",
                kind="chapter",
                status="streaming",
                content_text="已经保存的半截正文。",
                generated_words=10,
                request_summary={"targetWords": 800, "model": "basic"},
                created_at=now - timedelta(hours=1),
                updated_at=now - timedelta(hours=1),
            ),
            GenerationDraft(
                id="draft_fresh_recovery",
                user_id="recovery_writer",
                project_id="recovery_project",
                chapter_id="recovery_ch",
                kind="chapter",
                status="streaming",
                content_text="仍在写入的正文。",
                generated_words=10,
                request_summary={"targetWords": 800, "model": "basic"},
                created_at=now,
                updated_at=now,
            ),
        ]
    )
    user = await async_db_session.get(User, "recovery_writer")
    assert user is not None
    user.quota_remaining = 900
    async_db_session.add(
        UsageLog(
            user_id=user.id,
            project_id="recovery_project",
            feature="generate_chapter",
            model="gpt-test",
            reserved_credits=100,
            credits=0,
            status="reserved",
            detail={"reserved_split": {"monthly": 100, "purchased": 0}},
            reservation_expires_at=now - timedelta(hours=1),
        )
    )
    await async_db_session.commit()

    result = await recover_stale_generation_state(async_db_session, now=now)
    assert result == {"streaming_recovered": 1, "reservations_released": 1}

    stale = await async_db_session.get(GenerationDraft, "draft_stale_recovery")
    fresh = await async_db_session.get(GenerationDraft, "draft_fresh_recovery")
    assert stale is not None and stale.status == "failed"
    assert stale.error_code == "stream_timeout"
    assert stale.content_text == "已经保存的半截正文。"
    assert fresh is not None and fresh.status == "streaming"

    await async_db_session.refresh(user)
    assert user.quota_remaining == 1000
    log = (await async_db_session.execute(select(UsageLog))).scalar_one()
    assert log.status == "released"
    assert log.detail["release_reason"] == "reservation_expired"
