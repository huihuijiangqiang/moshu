from datetime import UTC, datetime, timedelta

from db.models_usage import UsageLog
from services.usage import (
    InsufficientCreditsError,
    UsageReservation,
    ensure_current_quota,
    release_reservation,
    reserve_generation,
    settle_generation,
)


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
            "credits": 2,
            "prompt_tokens": 100,
            "completion_tokens": 200,
        }
    ]
    assert len(payload["daily"]) == 14
    assert len(payload["recent"]) == 1
