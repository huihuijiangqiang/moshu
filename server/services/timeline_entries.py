"""CRUD rules for author-managed timeline entries."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_core import Chapter
from db.models_timeline import TimelineEntry


class TimelineEntryError(ValueError):
    pass


class TimelineEntryNotFoundError(LookupError):
    pass


class TimelineEntryConflictError(RuntimeError):
    def __init__(self, current_rev: int):
        super().__init__(f"Timeline entry revision mismatch; current revision is {current_rev}")
        self.current_rev = current_rev


def _clean_required(value: str, *, field: str, limit: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise TimelineEntryError(f"{field} cannot be empty")
    if len(cleaned) > limit:
        raise TimelineEntryError(f"{field} exceeds {limit} characters")
    return cleaned


def _clean_optional(value: str | None, *, field: str, limit: int) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > limit:
        raise TimelineEntryError(f"{field} exceeds {limit} characters")
    return cleaned


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _validated_values(
    *,
    title: str,
    detail: str | None,
    timeline_id: str,
    time_text: str | None,
    story_order: float | None,
    time_start: datetime | None,
    time_end: datetime | None,
) -> dict[str, object]:
    normalized_start = _aware(time_start)
    normalized_end = _aware(time_end)
    if normalized_end is not None and normalized_start is None:
        raise TimelineEntryError("time_start is required when time_end is set")
    if normalized_start is not None and normalized_end is not None and normalized_end < normalized_start:
        raise TimelineEntryError("time_end cannot be earlier than time_start")

    normalized_order = normalized_start.timestamp() if normalized_start is not None else story_order
    if normalized_order is not None and not math.isfinite(normalized_order):
        raise TimelineEntryError("story_order must be finite")

    return {
        "title": _clean_required(title, field="title", limit=200),
        "detail": _clean_optional(detail, field="detail", limit=2000),
        "timeline_id": _clean_required(timeline_id, field="timeline_id", limit=64),
        "time_text": _clean_optional(time_text, field="time_text", limit=200),
        "story_order": normalized_order,
        "time_start": normalized_start,
        "time_end": normalized_end,
    }


async def _validate_chapter(
    db: AsyncSession, *, project_id: str, chapter_id: str | None
) -> None:
    if chapter_id is None:
        return
    exists = await db.scalar(
        select(Chapter.id).where(
            Chapter.id == chapter_id,
            Chapter.project_id == project_id,
            Chapter.deleted_at.is_(None),
        )
    )
    if exists is None:
        raise TimelineEntryError("chapter does not belong to this project")


async def create_timeline_entry(
    db: AsyncSession,
    *,
    project_id: str,
    actor_id: str,
    chapter_id: str | None,
    title: str,
    detail: str | None,
    timeline_id: str,
    time_text: str | None,
    story_order: float | None,
    time_start: datetime | None,
    time_end: datetime | None,
) -> TimelineEntry:
    await _validate_chapter(db, project_id=project_id, chapter_id=chapter_id)
    values = _validated_values(
        title=title,
        detail=detail,
        timeline_id=timeline_id,
        time_text=time_text,
        story_order=story_order,
        time_start=time_start,
        time_end=time_end,
    )
    entry = TimelineEntry(
        id=f"te_{uuid4().hex[:24]}",
        project_id=project_id,
        chapter_id=chapter_id,
        created_by=actor_id,
        status="active",
        rev=1,
        **values,
    )
    db.add(entry)
    await db.flush()
    return entry


async def _locked_entry(
    db: AsyncSession, *, project_id: str, entry_id: str
) -> TimelineEntry:
    entry = await db.scalar(
        select(TimelineEntry)
        .where(TimelineEntry.id == entry_id, TimelineEntry.project_id == project_id)
        .with_for_update()
    )
    if entry is None or entry.status != "active":
        raise TimelineEntryNotFoundError("timeline entry not found")
    return entry


async def update_timeline_entry(
    db: AsyncSession,
    *,
    project_id: str,
    entry_id: str,
    expected_rev: int,
    chapter_id: str | None,
    title: str,
    detail: str | None,
    timeline_id: str,
    time_text: str | None,
    story_order: float | None,
    time_start: datetime | None,
    time_end: datetime | None,
) -> TimelineEntry:
    entry = await _locked_entry(db, project_id=project_id, entry_id=entry_id)
    if entry.rev != expected_rev:
        raise TimelineEntryConflictError(entry.rev)
    await _validate_chapter(db, project_id=project_id, chapter_id=chapter_id)
    values = _validated_values(
        title=title,
        detail=detail,
        timeline_id=timeline_id,
        time_text=time_text,
        story_order=story_order,
        time_start=time_start,
        time_end=time_end,
    )
    entry.chapter_id = chapter_id
    for key, value in values.items():
        setattr(entry, key, value)
    entry.rev += 1
    await db.flush()
    return entry


async def archive_timeline_entry(
    db: AsyncSession,
    *,
    project_id: str,
    entry_id: str,
    expected_rev: int,
) -> TimelineEntry:
    entry = await _locked_entry(db, project_id=project_id, entry_id=entry_id)
    if entry.rev != expected_rev:
        raise TimelineEntryConflictError(entry.rev)
    entry.status = "archived"
    entry.rev += 1
    await db.flush()
    return entry


__all__ = [
    "TimelineEntryConflictError",
    "TimelineEntryError",
    "TimelineEntryNotFoundError",
    "archive_timeline_entry",
    "create_timeline_entry",
    "update_timeline_entry",
]
