"""Provider-call telemetry stored in the existing usage ledger.

Platform-initiated extraction, summarization and embedding calls are operational
costs, not author purchases. They share ``usage_logs`` so token accounting has one
source of truth, while ``platform_event_id`` and ``detail.billing_scope`` keep them
out of author credit settlement.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_core import Project
from db.models_usage import UsageLog
from memory.tokenizer import Tokenizer

_tokenizer = Tokenizer()
logger = logging.getLogger(__name__)


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, int(value))


def provider_usage_event(
    *,
    model: str,
    usage: dict[str, Any] | None,
    prompt_text: str,
    completion_text: str = "",
    request_count: int = 1,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize one successful provider call without pretending estimates are exact."""
    raw = usage if isinstance(usage, dict) else {}
    prompt_tokens = _non_negative_int(raw.get("prompt_tokens"))
    completion_tokens = _non_negative_int(raw.get("completion_tokens"))
    token_details = raw.get("prompt_tokens_details")
    nested_cached = token_details.get("cached_tokens") if isinstance(token_details, dict) else None
    cached_tokens = _non_negative_int(nested_cached)
    if cached_tokens is None:
        cached_tokens = _non_negative_int(raw.get("cached_tokens"))
    estimated = prompt_tokens is None or completion_tokens is None
    if prompt_tokens is None:
        prompt_tokens = _tokenizer.count(prompt_text)
    if completion_tokens is None:
        completion_tokens = _tokenizer.count(completion_text)
    cached_tokens = min(prompt_tokens, cached_tokens or 0)
    return {
        "event_id": uuid.uuid4().hex,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "cached_tokens": cached_tokens,
        "completion_tokens": completion_tokens,
        "request_count": max(1, request_count),
        "estimated": estimated,
        "detail": dict(detail or {}),
    }


def chat_prompt_text(payload: dict[str, Any]) -> str:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return json.dumps(payload, ensure_ascii=False, default=str)
    parts: list[str] = []
    for message in messages:
        if isinstance(message, dict):
            parts.append(str(message.get("content") or ""))
    return "\n".join(parts)


async def record_platform_usage(
    db: AsyncSession,
    *,
    project_id: str,
    feature: str,
    events: Iterable[dict[str, Any]],
    task_id: str | None = None,
    consistency_run_id: int | None = None,
) -> int:
    """Append provider calls idempotently and return the number of inserted rows."""
    normalized = [event for event in events if isinstance(event, dict) and event.get("event_id")]
    if not normalized:
        return 0
    project = await db.get(Project, project_id)
    if project is None:
        logger.warning("Skipping platform usage recording: project %s not found", project_id)
        return 0
    event_ids = [str(event["event_id"]) for event in normalized]
    existing = set(
        (
            await db.execute(
                select(UsageLog.platform_event_id).where(UsageLog.platform_event_id.in_(event_ids))
            )
        ).scalars()
    )
    now = datetime.now(UTC)
    inserted = 0
    for event in normalized:
        event_id = str(event["event_id"])
        if event_id in existing:
            continue
        prompt_tokens = max(0, int(event.get("prompt_tokens") or 0))
        cached_tokens = min(prompt_tokens, max(0, int(event.get("cached_tokens") or 0)))
        completion_tokens = max(0, int(event.get("completion_tokens") or 0))
        event_detail = event.get("detail") if isinstance(event.get("detail"), dict) else {}
        db.add(
            UsageLog(
                user_id=project.owner_id,
                project_id=project.id,
                platform_event_id=event_id,
                provider_requests=max(1, int(event.get("request_count") or 1)),
                usage_estimated=bool(event.get("estimated", False)),
                feature=feature,
                model=str(event.get("model") or "unknown")[:100],
                prompt_tokens=prompt_tokens,
                cached_tokens=cached_tokens,
                completion_tokens=completion_tokens,
                reserved_credits=0,
                credits=0,
                status="completed",
                detail={
                    **event_detail,
                    "billing_scope": "platform",
                    "task_id": task_id,
                    "consistency_run_id": consistency_run_id,
                },
                timestamp=now,
                finalized_at=now,
            )
        )
        inserted += 1
    await db.flush()
    return inserted


async def record_account_platform_usage(
    db: AsyncSession,
    *,
    user_id: str,
    feature: str,
    events: Iterable[dict[str, Any]],
) -> int:
    """Record a platform-funded call made before a project exists."""
    normalized = [event for event in events if isinstance(event, dict) and event.get("event_id")]
    if not normalized:
        return 0
    event_ids = [str(event["event_id"]) for event in normalized]
    existing = set(
        (
            await db.execute(
                select(UsageLog.platform_event_id).where(UsageLog.platform_event_id.in_(event_ids))
            )
        ).scalars()
    )
    now = datetime.now(UTC)
    inserted = 0
    for event in normalized:
        event_id = str(event["event_id"])
        if event_id in existing:
            continue
        prompt_tokens = max(0, int(event.get("prompt_tokens") or 0))
        cached_tokens = min(prompt_tokens, max(0, int(event.get("cached_tokens") or 0)))
        event_detail = event.get("detail") if isinstance(event.get("detail"), dict) else {}
        db.add(
            UsageLog(
                user_id=user_id,
                project_id=None,
                platform_event_id=event_id,
                provider_requests=max(1, int(event.get("request_count") or 1)),
                usage_estimated=bool(event.get("estimated", False)),
                feature=feature,
                model=str(event.get("model") or "unknown")[:100],
                prompt_tokens=prompt_tokens,
                cached_tokens=cached_tokens,
                completion_tokens=max(0, int(event.get("completion_tokens") or 0)),
                reserved_credits=0,
                credits=0,
                status="completed",
                detail={**event_detail, "billing_scope": "platform"},
                timestamp=now,
                finalized_at=now,
            )
        )
        inserted += 1
    await db.flush()
    return inserted


__all__ = [
    "chat_prompt_text",
    "provider_usage_event",
    "record_account_platform_usage",
    "record_platform_usage",
]
