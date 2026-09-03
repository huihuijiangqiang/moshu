"""Transactional chapter-outline operations."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_consistency import ChapterOutlineRevision, ChapterOutlineState
from db.models_core import Chapter, ChapterBody
from domain.outlines import (
    BodyPolicy,
    OutlineContent,
    OutlineState,
    normalize_outline,
    plan_outline_transition,
    resolve_body_revision_marker,
)
from services.outbox import OutboxService


class ChapterNotFoundError(LookupError):
    pass


class OutlineRevisionConflictError(RuntimeError):
    def __init__(self, expected: int, actual: int, current: OutlineContent):
        self.expected = expected
        self.actual = actual
        self.current = current
        super().__init__(f"outline revision conflict: expected={expected}, actual={actual}")


@dataclass(frozen=True)
class OutlineResult:
    chapter_id: str
    content: OutlineContent
    state: OutlineState
    updated_at: str | None


def _domain_state(state: ChapterOutlineState | None) -> OutlineState:
    if state is None:
        return OutlineState()
    return OutlineState(
        revision=state.revision,
        body_needs_revision=state.body_needs_revision,
        marked_outline_rev=state.marked_outline_rev,
        marked_body_rev=state.marked_body_rev,
    )


def _result(chapter: Chapter, state: ChapterOutlineState | None) -> OutlineResult:
    note = state.note if state else ""
    updated_at = state.updated_at.isoformat() if state and state.updated_at else None
    return OutlineResult(
        chapter_id=chapter.id,
        content=OutlineContent(chapter.title, tuple(chapter.outline or []), note),
        state=_domain_state(state),
        updated_at=updated_at,
    )


async def get_outline(db: AsyncSession, chapter_id: str) -> OutlineResult:
    chapter_result = await db.execute(
        select(Chapter).where(Chapter.id == chapter_id, Chapter.deleted_at.is_(None))
    )
    chapter = chapter_result.scalar_one_or_none()
    if chapter is None:
        raise ChapterNotFoundError(chapter_id)

    state_result = await db.execute(select(ChapterOutlineState).where(ChapterOutlineState.chapter_id == chapter_id))
    return _result(chapter, state_result.scalar_one_or_none())


async def update_outline(
    db: AsyncSession,
    *,
    chapter_id: str,
    title: str,
    nodes: list[str],
    note: str,
    base_outline_revision: int,
    body_policy: BodyPolicy | None,
    created_by: str | None = None,
) -> OutlineResult:
    """Update plan state and history without exposing a body-write operation."""

    chapter_result = await db.execute(
        select(Chapter)
        .where(Chapter.id == chapter_id, Chapter.deleted_at.is_(None))
        .with_for_update()
    )
    chapter = chapter_result.scalar_one_or_none()
    if chapter is None:
        raise ChapterNotFoundError(chapter_id)

    state_result = await db.execute(select(ChapterOutlineState).where(ChapterOutlineState.chapter_id == chapter_id))
    state_row = state_result.scalar_one_or_none()
    current_state = _domain_state(state_row)
    current_content = OutlineContent(chapter.title, tuple(chapter.outline or []), state_row.note if state_row else "")

    if base_outline_revision != current_state.revision:
        raise OutlineRevisionConflictError(base_outline_revision, current_state.revision, current_content)

    requested = normalize_outline(title, nodes, note)
    if requested == current_content:
        return _result(chapter, state_row)

    body_result = await db.execute(select(ChapterBody).where(ChapterBody.chapter_id == chapter_id))
    body = body_result.scalar_one_or_none()
    body_rev = body.rev if body is not None else None
    transition = plan_outline_transition(
        current=current_content,
        state=current_state,
        requested=requested,
        body_rev=body_rev,
        body_policy=body_policy,
    )

    chapter.title = transition.content.title
    chapter.outline = list(transition.content.nodes)

    if state_row is None:
        state_row = ChapterOutlineState(chapter_id=chapter_id)
        db.add(state_row)
    state_row.revision = transition.state.revision
    state_row.note = transition.content.note
    state_row.body_needs_revision = transition.state.body_needs_revision
    state_row.marked_outline_rev = transition.state.marked_outline_rev
    state_row.marked_body_rev = transition.state.marked_body_rev

    db.add(
        ChapterOutlineRevision(
            chapter_id=chapter_id,
            revision=transition.state.revision,
            title=transition.content.title,
            nodes=list(transition.content.nodes),
            note=transition.content.note,
            body_policy=transition.body_policy.value,
            body_rev_at_change=transition.body_rev_at_change,
            created_by=created_by,
        )
    )
    await OutboxService.enqueue(
        db,
        topic="chapter.outline_updated",
        aggregate_id=chapter_id,
        aggregate_rev=transition.state.revision,
        payload={
            "project_id": chapter.project_id,
            "chapter_id": chapter_id,
            "outline_revision": transition.state.revision,
        },
    )
    await db.flush()
    return _result(chapter, state_row)


async def list_outline_revisions(
    db: AsyncSession,
    chapter_id: str,
    *,
    before_revision: int | None = None,
    limit: int = 50,
) -> list[ChapterOutlineRevision]:
    statement = select(ChapterOutlineRevision).where(ChapterOutlineRevision.chapter_id == chapter_id)
    if before_revision is not None:
        statement = statement.where(ChapterOutlineRevision.revision < before_revision)
    result = await db.execute(statement.order_by(ChapterOutlineRevision.revision.desc()).limit(limit))
    return list(result.scalars().all())


async def acknowledge_body_revision(
    db: AsyncSession,
    *,
    chapter_id: str,
    base_body_rev: int,
    addressed_outline_revision: int,
) -> OutlineResult:
    """Clear only the workflow marker; this operation cannot edit body content."""

    chapter_result = await db.execute(
        select(Chapter)
        .where(Chapter.id == chapter_id, Chapter.deleted_at.is_(None))
        .with_for_update()
    )
    chapter = chapter_result.scalar_one_or_none()
    if chapter is None:
        raise ChapterNotFoundError(chapter_id)

    state_result = await db.execute(
        select(ChapterOutlineState).where(ChapterOutlineState.chapter_id == chapter_id).with_for_update()
    )
    state_row = state_result.scalar_one_or_none()
    if state_row is None or not state_row.body_needs_revision:
        return _result(chapter, state_row)

    body_result = await db.execute(select(ChapterBody).where(ChapterBody.chapter_id == chapter_id))
    body = body_result.scalar_one_or_none()
    if body is None:
        raise RuntimeError("body revision marker exists without a chapter body")

    resolved = resolve_body_revision_marker(
        state=_domain_state(state_row),
        current_body_rev=body.rev,
        base_body_rev=base_body_rev,
        addressed_outline_revision=addressed_outline_revision,
    )
    state_row.body_needs_revision = resolved.body_needs_revision
    state_row.marked_outline_rev = resolved.marked_outline_rev
    state_row.marked_body_rev = resolved.marked_body_rev
    await OutboxService.enqueue(
        db,
        topic="chapter.body_revision_acknowledged",
        aggregate_id=chapter_id,
        aggregate_rev=state_row.revision,
        payload={
            "project_id": chapter.project_id,
            "chapter_id": chapter_id,
            "outline_revision": state_row.revision,
            "body_rev": body.rev,
        },
    )
    await db.flush()
    return _result(chapter, state_row)
