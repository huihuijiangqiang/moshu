"""Authenticated usage and quota reporting."""

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from db.models_core import User
from db.models_usage import UsageLog
from db.session import get_db
from services.usage import ensure_current_quota, get_credit_rates, month_start

router = APIRouter()

FEATURE_LABELS = {
    "generate_chapter": "一键成章",
    "generate_inline": "行内续写与润色",
    "consistency": "一致性守卫",
    "style_extract": "风格档抽取",
    "embedding": "设定向量化",
}
PLAN_LABELS = {"free": "免费版", "author": "作者版", "studio": "工作室版"}


@router.get("/summary")
async def usage_summary(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    current = await ensure_current_quota(db, user.id)
    now = datetime.now(UTC)
    period_start = month_start(now)
    rows = (
        await db.execute(
            select(UsageLog)
            .where(
                UsageLog.user_id == user.id,
                UsageLog.platform_event_id.is_(None),
                UsageLog.status == "completed",
                UsageLog.timestamp >= period_start,
            )
            .order_by(UsageLog.timestamp.desc(), UsageLog.id.desc())
        )
    ).scalars().all()
    by_feature: dict[str, dict[str, int]] = defaultdict(
        lambda: {"count": 0, "credits": 0, "prompt_tokens": 0, "completion_tokens": 0}
    )
    daily: dict[str, int] = defaultdict(int)
    for row in rows:
        item = by_feature[row.feature]
        item["count"] += 1
        item["credits"] += row.credits
        item["prompt_tokens"] += row.prompt_tokens
        item["completion_tokens"] += row.completion_tokens
        day = _iso_day(row.timestamp)
        daily[day] += row.credits
    first_day = (now - timedelta(days=13)).date()
    return {
        "plan": current.plan,
        "plan_label": PLAN_LABELS.get(current.plan, current.plan),
        "remaining": current.quota_remaining,
        "quota": current.quota_total,
        "spent": sum(row.credits for row in rows),
        "period_start": period_start.isoformat(),
        "resets_at": current.quota_resets_at.isoformat() if current.quota_resets_at else None,
        "rates": await get_credit_rates(db),
        "items": [
            {"feature": key, "label": FEATURE_LABELS.get(key, key), **values}
            for key, values in sorted(by_feature.items(), key=lambda pair: (-pair[1]["credits"], pair[0]))
        ],
        "daily": [
            {"date": (first_day + timedelta(days=offset)).isoformat(), "credits": daily[(first_day + timedelta(days=offset)).isoformat()]}
            for offset in range(14)
        ],
        "recent": [
            {
                "id": row.id,
                "feature": row.feature,
                "label": FEATURE_LABELS.get(row.feature, row.feature),
                "model": row.model,
                "credits": row.credits,
                "prompt_tokens": row.prompt_tokens,
                "cached_tokens": row.cached_tokens,
                "completion_tokens": row.completion_tokens,
                "timestamp": row.timestamp.isoformat(),
            }
            for row in rows[:20]
        ],
    }


def _iso_day(value: datetime) -> str:
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.date().isoformat()
