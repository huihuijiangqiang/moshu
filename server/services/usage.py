"""Transactional credit reservations, settlement, and monthly summaries."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_admin import SystemSetting
from db.models_core import User
from db.models_usage import UsageLog

DEFAULT_CREDIT_RATES = {
    "basic_input": 1,
    "basic_output": 2,
    "advanced_input": 4,
    "advanced_output": 8,
    "cached_percent": 20,
}


class InsufficientCreditsError(Exception):
    def __init__(self, *, required: int, remaining: int):
        self.required = required
        self.remaining = remaining
        super().__init__(f"requires {required} credits, only {remaining} remaining")


@dataclass(frozen=True)
class UsageReservation:
    log_id: int
    reserved_credits: int


def month_start(value: datetime) -> datetime:
    return datetime(value.year, value.month, 1, tzinfo=UTC)


def next_month_start(value: datetime) -> datetime:
    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    return datetime(year, month, 1, tzinfo=UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _validated_rates(value: dict | None) -> dict[str, int]:
    raw = value if isinstance(value, dict) else {}
    rates = {}
    for key, fallback in DEFAULT_CREDIT_RATES.items():
        candidate = raw.get(key, fallback)
        rates[key] = candidate if isinstance(candidate, int) and 0 <= candidate <= 100_000 else fallback
    rates["cached_percent"] = min(rates["cached_percent"], 100)
    return rates


async def get_credit_rates(db: AsyncSession) -> dict[str, int]:
    row = await db.get(SystemSetting, "credit_rates")
    return _validated_rates(row.value if row else None)


def calculate_credits(
    rates: dict[str, int],
    price_class: str,
    *,
    prompt_tokens: int,
    cached_tokens: int,
    completion_tokens: int,
) -> int:
    prefix = "advanced" if price_class == "advanced" else "basic"
    prompt = max(0, prompt_tokens)
    cached = min(prompt, max(0, cached_tokens))
    uncached = prompt - cached
    input_units = uncached * rates[f"{prefix}_input"]
    cached_units = cached * rates[f"{prefix}_input"] * rates["cached_percent"] / 100
    output_units = max(0, completion_tokens) * rates[f"{prefix}_output"]
    total = input_units + cached_units + output_units
    return math.ceil(total / 1000) if total > 0 else 0


def _reset_quota_if_due(user: User, now: datetime) -> None:
    reset_at = _as_utc(user.quota_resets_at) if user.quota_resets_at else None
    if reset_at is not None and reset_at <= now:
        user.quota_remaining = user.quota_total
    if reset_at is None or reset_at <= now:
        user.quota_resets_at = next_month_start(now)


def _same_billing_month(log: UsageLog, now: datetime) -> bool:
    return month_start(_as_utc(log.timestamp)) == month_start(now)


async def _release_expired_reservations(
    db: AsyncSession,
    user: User,
    now: datetime,
) -> None:
    rows = (
        await db.execute(
            select(UsageLog)
            .where(
                UsageLog.user_id == user.id,
                UsageLog.status == "reserved",
                UsageLog.reservation_expires_at <= now,
            )
            .with_for_update()
        )
    ).scalars().all()
    for log in rows:
        if _same_billing_month(log, now):
            user.quota_remaining += log.reserved_credits
        log.status = "released"
        log.credits = 0
        log.finalized_at = now
        log.detail = {**(log.detail or {}), "release_reason": "reservation_expired"}


async def reserve_generation(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str | None,
    feature: str,
    model: str,
    model_tier: str,
    prompt_tokens: int,
    target_words: int,
) -> UsageReservation:
    user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise ValueError("user not found")
    now = datetime.now(UTC)
    _reset_quota_if_due(user, now)
    await _release_expired_reservations(db, user, now)
    rates = await get_credit_rates(db)
    price_class = "advanced" if model_tier == "premium" else "basic"
    max_completion_tokens = min(32_000, max(800, int(target_words * 1.8)))
    reserved = calculate_credits(
        rates,
        price_class,
        prompt_tokens=prompt_tokens,
        cached_tokens=0,
        completion_tokens=max_completion_tokens,
    )
    if reserved > user.quota_remaining:
        remaining = user.quota_remaining
        await db.rollback()
        raise InsufficientCreditsError(required=reserved, remaining=remaining)
    user.quota_remaining -= reserved
    log = UsageLog(
        user_id=user.id,
        project_id=project_id,
        feature=feature,
        model=model,
        prompt_tokens=prompt_tokens,
        reserved_credits=reserved,
        credits=0,
        status="reserved",
        detail={"price_class": price_class, "rates": rates},
        timestamp=now,
        reservation_expires_at=now + timedelta(hours=1),
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return UsageReservation(log_id=log.id, reserved_credits=reserved)


async def settle_generation(
    db: AsyncSession,
    reservation: UsageReservation,
    *,
    run_id: str | None,
    prompt_tokens: int,
    cached_tokens: int,
    completion_tokens: int,
) -> int:
    log = await db.scalar(select(UsageLog).where(UsageLog.id == reservation.log_id).with_for_update())
    if log is None or log.status != "reserved":
        return log.credits if log else 0
    user = await db.scalar(select(User).where(User.id == log.user_id).with_for_update())
    if user is None:
        raise ValueError("usage user not found")
    rates = _validated_rates((log.detail or {}).get("rates"))
    actual = calculate_credits(
        rates,
        (log.detail or {}).get("price_class", "basic"),
        prompt_tokens=prompt_tokens,
        cached_tokens=cached_tokens,
        completion_tokens=completion_tokens,
    )
    if _same_billing_month(log, datetime.now(UTC)):
        maximum_charge = log.reserved_credits + user.quota_remaining
        charged = min(actual, maximum_charge)
        user.quota_remaining += log.reserved_credits - charged
    else:
        # The old period has closed. Record actual usage without carrying an old
        # reservation refund or overage into the newly reset monthly balance.
        charged = min(actual, log.reserved_credits)
    log.run_id = run_id
    log.prompt_tokens = max(0, prompt_tokens)
    log.cached_tokens = min(log.prompt_tokens, max(0, cached_tokens))
    log.completion_tokens = max(0, completion_tokens)
    log.credits = charged
    log.status = "completed"
    log.reservation_expires_at = None
    log.finalized_at = datetime.now(UTC)
    log.detail = {**(log.detail or {}), "calculated_credits": actual, "unbilled_credits": actual - charged}
    return charged


async def release_reservation(db: AsyncSession, reservation: UsageReservation, *, reason: str) -> None:
    log = await db.scalar(select(UsageLog).where(UsageLog.id == reservation.log_id).with_for_update())
    if log is None or log.status != "reserved":
        return
    user = await db.scalar(select(User).where(User.id == log.user_id).with_for_update())
    now = datetime.now(UTC)
    if user is not None and _same_billing_month(log, now):
        user.quota_remaining += log.reserved_credits
    log.status = "released"
    log.reservation_expires_at = None
    log.credits = 0
    log.finalized_at = now
    log.detail = {**(log.detail or {}), "release_reason": reason[:100]}


async def ensure_current_quota(db: AsyncSession, user_id: str) -> User:
    user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise ValueError("user not found")
    before = (user.quota_remaining, user.quota_resets_at)
    now = datetime.now(UTC)
    _reset_quota_if_due(user, now)
    await _release_expired_reservations(db, user, now)
    if before != (user.quota_remaining, user.quota_resets_at):
        await db.commit()
    return user
