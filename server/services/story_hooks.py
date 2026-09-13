"""Durable chapter-ending hook debts and their payoff lifecycle."""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_core import Chapter
from db.models_long_generation import StoryHook


class StoryHookError(RuntimeError):
    pass


class StoryHookNotFoundError(StoryHookError):
    pass


class StoryHookConflictError(StoryHookError):
    def __init__(self, current_revision: int):
        self.current_revision = current_revision
        super().__init__("story hook revision conflict")


class StoryHookValidationError(StoryHookError):
    pass


def story_hook_signature(hook_type: str, concrete_event: str, unresolved_question: str) -> str:
    normalized = "|".join(
        re.sub(r"\s+", "", value).casefold() for value in (hook_type, concrete_event, unresolved_question)
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def _chapter_in_project(
    db: AsyncSession,
    chapter_id: str,
    project_id: str,
    *,
    include_deleted: bool = False,
) -> Chapter:
    clauses = [Chapter.id == chapter_id, Chapter.project_id == project_id]
    if not include_deleted:
        clauses.append(Chapter.deleted_at.is_(None))
    chapter = await db.scalar(select(Chapter).where(*clauses))
    if chapter is None:
        raise StoryHookValidationError("chapter does not belong to this project")
    return chapter


async def _chapter_number(db: AsyncSession, chapter: Chapter) -> int:
    return int(
        await db.scalar(
            select(func.count(Chapter.id)).where(
                Chapter.project_id == chapter.project_id,
                Chapter.idx <= chapter.idx,
                Chapter.deleted_at.is_(None),
            )
        )
        or 1
    )


async def create_story_hook(
    db: AsyncSession,
    *,
    project_id: str,
    source_chapter_id: str,
    hook_type: str,
    concrete_event: str,
    unresolved_question: str,
    payoff_by_chapter: int | None = None,
) -> StoryHook:
    source = await _chapter_in_project(db, source_chapter_id, project_id)
    source_number = await _chapter_number(db, source)
    if payoff_by_chapter is not None and payoff_by_chapter <= source_number:
        raise StoryHookValidationError("payoff chapter must come after the source chapter")
    signature = story_hook_signature(hook_type, concrete_event, unresolved_question)
    duplicate = await db.scalar(
        select(StoryHook).where(
            StoryHook.project_id == project_id,
            StoryHook.novelty_signature == signature,
        )
    )
    if duplicate is not None:
        raise StoryHookValidationError("an identical hook already exists")
    hook = StoryHook(
        id=uuid.uuid4().hex,
        project_id=project_id,
        source_chapter_id=source.id,
        hook_type=hook_type.strip(),
        concrete_event=concrete_event.strip(),
        unresolved_question=unresolved_question.strip(),
        payoff_by_chapter=payoff_by_chapter,
        status="open",
        novelty_signature=signature,
    )
    db.add(hook)
    await db.flush()
    return hook


async def list_story_hooks(
    db: AsyncSession,
    *,
    project_id: str,
    statuses: set[str] | None = None,
    limit: int = 200,
) -> list[StoryHook]:
    query = select(StoryHook).where(StoryHook.project_id == project_id)
    if statuses:
        query = query.where(StoryHook.status.in_(statuses))
    result = await db.execute(
        query.order_by(
            StoryHook.payoff_by_chapter.is_(None),
            StoryHook.payoff_by_chapter,
            StoryHook.created_at,
        ).limit(limit)
    )
    return list(result.scalars())


async def update_story_hook(
    db: AsyncSession,
    *,
    hook_id: str,
    project_id: str,
    expected_revision: int,
    changes: dict[str, Any],
) -> StoryHook:
    hook = await db.scalar(
        select(StoryHook).where(StoryHook.id == hook_id, StoryHook.project_id == project_id).with_for_update()
    )
    if hook is None:
        raise StoryHookNotFoundError(hook_id)
    if hook.revision != expected_revision:
        raise StoryHookConflictError(hook.revision)

    payoff_chapter_id = changes.get("payoff_chapter_id", hook.payoff_chapter_id)
    if payoff_chapter_id is not None:
        await _chapter_in_project(db, payoff_chapter_id, project_id)
    payoff_by_chapter = changes.get("payoff_by_chapter", hook.payoff_by_chapter)
    source = await _chapter_in_project(
        db,
        hook.source_chapter_id,
        project_id,
        include_deleted=True,
    )
    source_number = await _chapter_number(db, source)
    if payoff_by_chapter is not None and payoff_by_chapter <= source_number:
        raise StoryHookValidationError("payoff chapter must come after the source chapter")

    status = changes.get("status", hook.status)
    resolution = str(changes.get("resolution", hook.resolution) or "").strip()
    if status == "resolved" and (not payoff_chapter_id or not resolution):
        raise StoryHookValidationError("resolved hooks require a payoff chapter and resolution")
    for field in ("hook_type", "concrete_event", "unresolved_question"):
        if field in changes:
            value = str(changes[field]).strip()
            if not value:
                raise StoryHookValidationError(f"{field} cannot be empty")
            setattr(hook, field, value)
    hook.payoff_chapter_id = payoff_chapter_id
    hook.payoff_by_chapter = payoff_by_chapter
    hook.status = status
    hook.resolution = resolution
    signature = story_hook_signature(
        hook.hook_type,
        hook.concrete_event,
        hook.unresolved_question,
    )
    duplicate = await db.scalar(
        select(StoryHook).where(
            StoryHook.project_id == project_id,
            StoryHook.novelty_signature == signature,
            StoryHook.id != hook.id,
        )
    )
    if duplicate is not None:
        raise StoryHookValidationError("an identical hook already exists")
    hook.novelty_signature = signature
    hook.revision += 1
    await db.flush()
    return hook


__all__ = [
    "StoryHookConflictError",
    "StoryHookNotFoundError",
    "StoryHookValidationError",
    "create_story_hook",
    "list_story_hooks",
    "story_hook_signature",
    "update_story_hook",
]
