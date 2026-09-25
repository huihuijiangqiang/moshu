from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from db.models_core import User
from db.models_usage import CreditGrant, UsageLog
from services.provider_usage import provider_usage_event, record_platform_usage
from services.usage import (
    InsufficientCreditsError,
    UsageReservation,
    ensure_current_quota,
    release_reservation,
    reserve_fixed_credits,
    reserve_generation,
    settle_fixed_credits,
    settle_generation,
)


async def test_fixed_image_price_reserves_and_settles_without_token_formula(async_db_session, seed_project):
    await seed_project(user_id="image_user", project_id="image_project")
    reservation = await reserve_fixed_credits(
        async_db_session, user_id="image_user", project_id="image_project",
        feature="comic_image", model="gpt-image-2", credits=23,
    )
    user = await ensure_current_quota(async_db_session, "image_user")
    assert reservation.reserved_credits == 23
    assert user.quota_remaining == 977

    assert await settle_fixed_credits(async_db_session, reservation) == 23
    assert await settle_fixed_credits(async_db_session, reservation) == 23
    log = await async_db_session.get(UsageLog, reservation.log_id)
    assert log.status == "completed" and log.credits == 23
    assert log.prompt_tokens == 0 and log.completion_tokens == 0
    assert log.detail["billing_mode"] == "fixed_image"
    assert user.quota_remaining == 977


async def test_fixed_image_reservation_can_share_job_transaction(async_db_session, seed_project):
    await seed_project(user_id="image_atomic", project_id="image_atomic_project")
    await async_db_session.commit()
    reservation = await reserve_fixed_credits(
        async_db_session, user_id="image_atomic", project_id="image_atomic_project",
        feature="comic_image", model="gpt-image-2", credits=23, commit=False,
    )
    assert (await async_db_session.get(UsageLog, reservation.log_id)).status == "reserved"
    assert (await async_db_session.get(User, "image_atomic")).quota_remaining == 977

    await async_db_session.rollback()
    assert await async_db_session.get(UsageLog, reservation.log_id) is None
    assert (await async_db_session.get(User, "image_atomic")).quota_remaining == 1000


async def test_failed_fixed_image_restores_monthly_and_purchased_balance(async_db_session, seed_project):
    await seed_project(user_id="image_refund", project_id="image_refund_project")
    user = await ensure_current_quota(async_db_session, "image_refund")
    user.quota_remaining = 2
    user.quota_total = 2
    user.purchased_credits_remaining = 10
    grant = CreditGrant(
        id="image_grant", user_id=user.id, kind="purchase", credits=10, remaining_credits=10,
    )
    async_db_session.add(grant)
    await async_db_session.commit()

    reservation = await reserve_fixed_credits(
        async_db_session, user_id=user.id, project_id="image_refund_project",
        feature="comic_image", model="gpt-image-2", credits=8,
    )
    assert user.quota_remaining == 0 and user.purchased_credits_remaining == 4
    await release_reservation(async_db_session, reservation, reason="provider_error")
    await async_db_session.commit()
    assert user.quota_remaining == 2 and user.purchased_credits_remaining == 10
    assert grant.remaining_credits == 10

    next_reservation = await reserve_fixed_credits(
        async_db_session, user_id=user.id, project_id="image_refund_project",
        feature="comic_image", model="gpt-image-2", credits=8,
    )
    assert await settle_fixed_credits(async_db_session, next_reservation) == 8
    await async_db_session.commit()
    assert user.quota_remaining == 0 and user.purchased_credits_remaining == 4
    assert grant.remaining_credits == 4


async def test_fixed_image_rejects_missing_price_or_balance(async_db_session, seed_project):
    await seed_project(user_id="image_low", project_id="image_low_project")
    with pytest.raises(ValueError, match="positive"):
        await reserve_fixed_credits(
            async_db_session, user_id="image_low", project_id="image_low_project",
            feature="comic_image", model="gpt-image-2", credits=0,
        )
    user = await ensure_current_quota(async_db_session, "image_low")
    user.quota_remaining = 1
    await async_db_session.commit()
    try:
        await reserve_fixed_credits(
            async_db_session, user_id=user.id, project_id="image_low_project",
            feature="comic_image", model="gpt-image-2", credits=3,
        )
    except InsufficientCreditsError as error:
        assert (error.required, error.remaining) == (3, 1)
    else:
        raise AssertionError("expected insufficient credit rejection")


async def test_generation_reservation_settles_actual_cost_and_refunds_difference(
    async_db_session, seed_project
):
    await seed_project(user_id="billing_user", project_id="billing_project")
    reservation = await reserve_generation(
        async_db_session,
        user_id="billing_user",
        project_id="billing_project",
        feature="generate_chapter",
        model="test-model",
        model_tier="main",
        prompt_tokens=123,
        target_words=1200,
    )
    user = await ensure_current_quota(async_db_session, "billing_user")
    assert reservation.reserved_credits == 5
    assert user.quota_remaining == 995

    charged = await settle_generation(
        async_db_session,
        reservation,
        run_id="future_run",
        prompt_tokens=123,
        cached_tokens=10,
        completion_tokens=18,
    )
    # The isolated service test does not create a GenerationRun, so avoid flushing
    # the optional FK and inspect the in-memory settlement state.
    log = await async_db_session.get(UsageLog, reservation.log_id)
    assert charged == 1
    assert log is not None and log.status == "completed"
    assert log.cached_tokens == 10
    assert user.quota_remaining == 999


async def test_failed_generation_releases_full_reservation(async_db_session, seed_project):
    await seed_project(user_id="refund_user", project_id="refund_project")
    reservation = await reserve_generation(
        async_db_session,
        user_id="refund_user",
        project_id="refund_project",
        feature="generate_inline",
        model="test-model",
        model_tier="premium",
        prompt_tokens=300,
        target_words=600,
    )
    await release_reservation(async_db_session, reservation, reason="provider_error")
    await async_db_session.commit()

    user = await ensure_current_quota(async_db_session, "refund_user")
    log = await async_db_session.get(UsageLog, reservation.log_id)
    assert user.quota_remaining == 1000
    assert log is not None and log.status == "released"
    assert log.credits == 0


async def test_reservation_rejects_insufficient_balance(async_db_session, seed_project):
    await seed_project(user_id="low_balance", project_id="low_project")
    user = await ensure_current_quota(async_db_session, "low_balance")
    user.quota_remaining = 1
    user.quota_total = 1
    await async_db_session.commit()
    try:
        await reserve_generation(
            async_db_session,
            user_id="low_balance",
            project_id="low_project",
            feature="generate_chapter",
            model="test-model",
            model_tier="premium",
            prompt_tokens=1000,
            target_words=20_000,
        )
    except InsufficientCreditsError as error:
        assert error.required > error.remaining
    else:
        raise AssertionError("expected insufficient credit rejection")


async def test_expired_monthly_quota_resets_lazily(async_db_session, make_user):
    user = make_user(
        "monthly_user",
        quota_remaining=12,
        quota_total=500,
        quota_resets_at=datetime.now(UTC) - timedelta(days=1),
    )
    async_db_session.add(user)
    await async_db_session.commit()

    current = await ensure_current_quota(async_db_session, user.id)

    assert current.quota_remaining == 500
    assert current.quota_resets_at > datetime.now(UTC)


async def test_expired_reservation_is_released_on_next_usage_read(
    async_db_session, make_user
):
    user = make_user("expired_hold", quota_remaining=90, quota_total=100)
    async_db_session.add(user)
    await async_db_session.flush()
    log = UsageLog(
        user_id=user.id,
        feature="generate_chapter",
        reserved_credits=10,
        credits=0,
        status="reserved",
        detail={},
        timestamp=datetime.now(UTC) - timedelta(hours=2),
        reservation_expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    async_db_session.add(log)
    await async_db_session.commit()

    current = await ensure_current_quota(async_db_session, user.id)

    assert current.quota_remaining == 100
    assert log.status == "released"
    assert log.detail["release_reason"] == "reservation_expired"


async def test_old_month_reservation_does_not_overcredit_new_month(
    async_db_session, make_user
):
    now = datetime.now(UTC)
    previous_month = now.replace(day=1) - timedelta(days=1)
    user = make_user(
        "cross_month",
        quota_remaining=100,
        quota_total=100,
        quota_resets_at=now.replace(day=28) + timedelta(days=10),
    )
    async_db_session.add(user)
    await async_db_session.flush()
    log = UsageLog(
        user_id=user.id,
        feature="generate_chapter",
        prompt_tokens=100,
        reserved_credits=10,
        credits=0,
        status="reserved",
        detail={"price_class": "basic", "rates": {"basic_input": 1, "basic_output": 2, "advanced_input": 4, "advanced_output": 8, "cached_percent": 20}},
        timestamp=previous_month,
    )
    async_db_session.add(log)
    await async_db_session.commit()
    await async_db_session.refresh(log)
    reservation = UsageReservation(log_id=log.id, reserved_credits=10)

    charged = await settle_generation(
        async_db_session,
        reservation,
        run_id="cross_month_run",
        prompt_tokens=100,
        cached_tokens=0,
        completion_tokens=100,
    )

    assert charged == 1
    assert user.quota_remaining == 100


async def test_usage_summary_returns_only_current_completed_events(
    app_client, async_db_session, make_user, auth_headers
):
    user = make_user("summary_user", plan="author", quota_remaining=987, quota_total=1000)
    async_db_session.add(user)
    await async_db_session.flush()
    async_db_session.add_all(
        [
            UsageLog(
                user_id=user.id,
                feature="generate_chapter",
                model="model-a",
                prompt_tokens=100,
                completion_tokens=200,
                reserved_credits=2,
                credits=2,
                status="completed",
                detail={},
                timestamp=datetime.now(UTC),
            ),
            UsageLog(
                user_id=user.id,
                feature="generate_chapter",
                reserved_credits=9,
                credits=0,
                status="released",
                detail={},
                timestamp=datetime.now(UTC),
            ),
            UsageLog(
                user_id=user.id,
                feature="generate_inline",
                credits=11,
                status="completed",
                detail={},
                timestamp=datetime.now(UTC) - timedelta(days=40),
            ),
            UsageLog(
                user_id=user.id,
                platform_event_id="platform-event",
                feature="consistency_summary",
                model="model-platform",
                prompt_tokens=800,
                completion_tokens=100,
                credits=0,
                status="completed",
                detail={"billing_scope": "platform"},
                timestamp=datetime.now(UTC),
            ),
        ]
    )
    await async_db_session.commit()

    response = await app_client.get("/usage/summary", headers=auth_headers(user.id))

    assert response.status_code == 200
    payload = response.json()
    assert payload["remaining"] == 987
    assert payload["spent"] == 2
    assert payload["items"] == [
        {
            "feature": "generate_chapter",
            "label": "一键成章",
            "count": 1,
            "user_key_count": 0,
            "credits": 2,
            "prompt_tokens": 100,
            "completion_tokens": 200,
        }
    ]
    assert len(payload["daily"]) == 14
    assert len(payload["recent"]) == 1
    assert payload["recent"][0]["billing_mode"] == "platform"


async def test_platform_usage_events_are_idempotent_and_never_charge_author(
    async_db_session, seed_project
):
    await seed_project(user_id="platform_owner", project_id="platform_project")
    event = provider_usage_event(
        model="model-a",
        usage={
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 30},
        },
        prompt_text="prompt",
        completion_text="completion",
        request_count=2,
        detail={"provider": "chat_completions"},
    )

    first = await record_platform_usage(
        async_db_session,
        project_id="platform_project",
        feature="consistency_extract",
        events=[event],
        task_id="task-a",
        consistency_run_id=12,
    )
    second = await record_platform_usage(
        async_db_session,
        project_id="platform_project",
        feature="consistency_extract",
        events=[event],
        task_id="task-a",
        consistency_run_id=12,
    )
    await async_db_session.commit()

    rows = (
        await async_db_session.execute(
            select(UsageLog).where(UsageLog.platform_event_id == event["event_id"])
        )
    ).scalars().all()
    assert first == 1
    assert second == 0
    assert len(rows) == 1
    assert rows[0].credits == 0
    assert rows[0].cached_tokens == 30
    assert rows[0].detail["billing_scope"] == "platform"
    assert rows[0].provider_requests == 2
    assert rows[0].usage_estimated is False
