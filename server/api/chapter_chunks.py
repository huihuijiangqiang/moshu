"""Project-level operations for versioned chapter body embeddings."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import ProjectPermission, get_current_user, verify_project_permission
from db.models_consistency import OutboxEvent
from db.models_core import User
from db.session import get_db
from services.chapter_chunks import (
    current_chunk_states_by_chapter,
    mark_current_chapter_chunks_for_reindex,
    project_body_revisions,
    project_chunk_index_status,
)
from services.outbox import OutboxService


router = APIRouter()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ChunkCountResponse(BaseModel):
    total: int
    ready: int
    pending: int
    failed: int
    stale: int


class ChapterCountResponse(BaseModel):
    total: int
    indexed: int
    unindexed: int
    queued: int


class ProjectChunkIndexResponse(BaseModel):
    project_id: str
    chunks: ChunkCountResponse
    chapters: ChapterCountResponse


class ProjectChunkReindexResponse(BaseModel):
    project_id: str
    chapter_count: int
    queued: int
    already_queued: int
    requeued: int


async def _queued_current_chapter_count(
    db: AsyncSession, *, project_id: str, revisions: list[tuple[str, int]]
) -> int:
    if not revisions:
        return 0
    chapter_ids = [chapter_id for chapter_id, _ in revisions]
    clauses = [
        (OutboxEvent.aggregate_id == chapter_id) & (OutboxEvent.aggregate_rev == body_rev)
        for chapter_id, body_rev in revisions
    ]
    return int(
        await db.scalar(
            select(func.count(func.distinct(OutboxEvent.aggregate_id))).where(
                OutboxEvent.topic.in_(
                    ("chapter.chunk_embedding_requested", "chapter.chunk_reindex_requested")
                ),
                OutboxEvent.aggregate_id.in_(chapter_ids),
                OutboxEvent.status.in_(("pending", "dispatching")),
                or_(*clauses),
            )
        )
        or 0
    )


@router.get("/{project_id}/chapter-chunks/status", response_model=ProjectChunkIndexResponse)
async def get_project_chunk_index_status(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectChunkIndexResponse:
    await verify_project_permission(project_id, ProjectPermission.VIEW, user, db)
    status = await project_chunk_index_status(db, project_id=project_id)
    revisions = await project_body_revisions(db, project_id=project_id)
    queued = await _queued_current_chapter_count(db, project_id=project_id, revisions=revisions)
    return ProjectChunkIndexResponse(
        project_id=project_id,
        chunks=ChunkCountResponse(
            total=status.total,
            ready=status.ready,
            pending=status.pending,
            failed=status.failed,
            stale=status.stale,
        ),
        chapters=ChapterCountResponse(
            total=status.chapter_count,
            indexed=status.indexed_chapter_count,
            unindexed=max(0, status.chapter_count - status.indexed_chapter_count),
            queued=queued,
        ),
    )


@router.post("/{project_id}/chapter-chunks/reindex", response_model=ProjectChunkReindexResponse, status_code=202)
async def reindex_project_chapter_chunks(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectChunkReindexResponse:
    """Queue every active body revision; no embedding call occurs in this request."""
    await verify_project_permission(project_id, ProjectPermission.MANAGE_PROJECT, user, db)
    revisions = await project_body_revisions(db, project_id=project_id)
    current_states = await current_chunk_states_by_chapter(db, project_id=project_id)
    # A body save queues ``chapter.chunk_embedding_requested`` while this
    # project-level operation queues the forceful reindex variant.  Treat both
    # topics as the same in-flight work item so a user clicking reindex while a
    # fresh save is dispatching cannot create duplicate embedding jobs.
    existing_rows = list(
        (
            await db.execute(
                select(OutboxEvent).where(
                    OutboxEvent.topic.in_(
                        ("chapter.chunk_reindex_requested", "chapter.chunk_embedding_requested")
                    ),
                    OutboxEvent.aggregate_id.in_([chapter_id for chapter_id, _ in revisions]),
                )
            )
        )
        .scalars()
        .all()
    ) if revisions else []
    # Prefer an active event over a terminal one; among events with the same
    # lifecycle state prefer the forceful reindex variant.  This handles a
    # stale dead-letter reindex alongside a newer body-save event without
    # reactivating both tasks.
    def _event_priority(event: OutboxEvent) -> tuple[int, int]:
        lifecycle = {"pending": 0, "dispatching": 0, "sent": 1}.get(event.status, 2)
        force = 0 if event.topic == "chapter.chunk_reindex_requested" else 1
        return lifecycle, force

    existing: dict[tuple[str, int], OutboxEvent] = {}
    for row in existing_rows:
        key = (row.aggregate_id, row.aggregate_rev)
        current = existing.get(key)
        if current is None or _event_priority(row) < _event_priority(current):
            existing[key] = row
    queued = already_queued = requeued = 0
    for chapter_id, body_rev in revisions:
        payload = {
            "project_id": project_id,
            "chapter_id": chapter_id,
            "body_rev": body_rev,
            "force": True,
        }
        event = existing.get((chapter_id, body_rev))
        if event is None:
            await mark_current_chapter_chunks_for_reindex(
                db, project_id=project_id, chapter_id=chapter_id, body_rev=body_rev
            )
            await OutboxService.enqueue(
                db,
                topic="chapter.chunk_reindex_requested",
                aggregate_id=chapter_id,
                aggregate_rev=body_rev,
                payload=payload,
            )
            queued += 1
        elif event.status in {"pending", "dispatching"}:
            already_queued += 1
        elif (
            event.status == "sent"
            and current_states.get((chapter_id, body_rev), set()) <= {"pending"}
            and event.sent_at is not None
            and _as_utc(event.sent_at) > datetime.now(UTC) - timedelta(minutes=10)
        ):
            # Broker accepted the task but the worker has not completed local
            # indexing yet. Reusing the sent event here would publish duplicate work.
            already_queued += 1
        else:
            await mark_current_chapter_chunks_for_reindex(
                db, project_id=project_id, chapter_id=chapter_id, body_rev=body_rev
            )
            if await OutboxService.requeue_terminal(db, event):
                requeued += 1
    await db.commit()
    return ProjectChunkReindexResponse(
        project_id=project_id,
        chapter_count=len(revisions),
        queued=queued,
        already_queued=already_queued,
        requeued=requeued,
    )
