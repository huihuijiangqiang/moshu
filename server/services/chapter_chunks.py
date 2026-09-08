"""Versioned chapter-body chunking, invalidation, and semantic retrieval."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models_chapter_chunks import ChapterChunk
from db.models_core import Chapter, ChapterBody
from services.chunking import TextChunk, chunk_html
from services.providers import EmbeddingProvider


@dataclass(frozen=True)
class ProjectChunkIndexStatus:
    project_id: str
    chapter_count: int
    indexed_chapter_count: int
    total: int
    ready: int
    pending: int
    failed: int
    stale: int


async def project_chunk_index_status(
    db: AsyncSession, *, project_id: str
) -> ProjectChunkIndexStatus:
    """Aggregate chunk health while treating every non-head row as stale."""
    chapter_count = int(
        await db.scalar(
            select(func.count(Chapter.id))
            .join(ChapterBody, ChapterBody.chapter_id == Chapter.id)
            .where(Chapter.project_id == project_id, Chapter.deleted_at.is_(None))
        )
        or 0
    )
    current = ChapterChunk.body_rev == ChapterBody.rev
    active = (
        select(
            func.count(ChapterChunk.id),
            func.coalesce(func.sum(case((current & (ChapterChunk.status == "ready"), 1), else_=0)), 0),
            func.coalesce(func.sum(case((current & (ChapterChunk.status == "pending"), 1), else_=0)), 0),
            func.coalesce(func.sum(case((current & (ChapterChunk.status == "failed"), 1), else_=0)), 0),
            func.coalesce(
                func.sum(case((or_(~current, ChapterChunk.status == "stale"), 1), else_=0)),
                0,
            ),
        )
        .select_from(ChapterChunk)
        .join(Chapter, Chapter.id == ChapterChunk.chapter_id)
        .join(ChapterBody, ChapterBody.chapter_id == ChapterChunk.chapter_id)
        .where(
            ChapterChunk.project_id == project_id,
            Chapter.project_id == project_id,
            Chapter.deleted_at.is_(None),
        )
    )
    row = (await db.execute(active)).one()
    ready_chapters = (
        select(ChapterChunk.chapter_id)
        .join(Chapter, Chapter.id == ChapterChunk.chapter_id)
        .join(ChapterBody, ChapterBody.chapter_id == ChapterChunk.chapter_id)
        .where(
            ChapterChunk.project_id == project_id,
            Chapter.project_id == project_id,
            Chapter.deleted_at.is_(None),
            ChapterChunk.body_rev == ChapterBody.rev,
            ChapterChunk.status != "stale",
        )
        .group_by(ChapterChunk.chapter_id)
        .having(func.count(ChapterChunk.id) > 0)
        .having(
            func.sum(
                case(
                    (
                        (ChapterChunk.status != "ready")
                        | ChapterChunk.embedding.is_(None),
                        1,
                    ),
                    else_=0,
                )
            )
            == 0
        )
        .subquery()
    )
    indexed_chapter_count = int(
        await db.scalar(select(func.count()).select_from(ready_chapters)) or 0
    )
    return ProjectChunkIndexStatus(
        project_id=project_id,
        chapter_count=chapter_count,
        indexed_chapter_count=indexed_chapter_count,
        total=int(row[0] or 0),
        ready=int(row[1] or 0),
        pending=int(row[2] or 0),
        failed=int(row[3] or 0),
        stale=int(row[4] or 0),
    )


async def project_body_revisions(db: AsyncSession, *, project_id: str) -> list[tuple[str, int]]:
    """Return active chapter heads in stable order for batch outbox scheduling."""
    result = await db.execute(
        select(Chapter.id, ChapterBody.rev)
        .join(ChapterBody, ChapterBody.chapter_id == Chapter.id)
        .where(Chapter.project_id == project_id, Chapter.deleted_at.is_(None))
        .order_by(Chapter.idx, Chapter.id)
    )
    return [(str(chapter_id), int(body_rev)) for chapter_id, body_rev in result.all()]


async def current_chunk_states_by_chapter(
    db: AsyncSession, *, project_id: str
) -> dict[tuple[str, int], set[str]]:
    """Return only head-revision states; historical rows cannot affect scheduling."""
    result = await db.execute(
        select(ChapterChunk.chapter_id, ChapterChunk.body_rev, ChapterChunk.status)
        .join(Chapter, Chapter.id == ChapterChunk.chapter_id)
        .join(ChapterBody, ChapterBody.chapter_id == ChapterChunk.chapter_id)
        .where(
            ChapterChunk.project_id == project_id,
            Chapter.project_id == project_id,
            Chapter.deleted_at.is_(None),
            ChapterChunk.body_rev == ChapterBody.rev,
            ChapterChunk.status != "stale",
        )
    )
    states: dict[tuple[str, int], set[str]] = {}
    for chapter_id, body_rev, status in result.all():
        states.setdefault((str(chapter_id), int(body_rev)), set()).add(str(status))
    return states


async def mark_current_chapter_chunks_for_reindex(
    db: AsyncSession, *, project_id: str, chapter_id: str, body_rev: int
) -> int:
    """Invalidate current vectors in the same transaction that queues rebuild work."""
    result = await db.execute(
        update(ChapterChunk)
        .where(
            ChapterChunk.project_id == project_id,
            ChapterChunk.chapter_id == chapter_id,
            ChapterChunk.body_rev == body_rev,
            ChapterChunk.status != "stale",
        )
        .values(
            status="pending",
            embedding=None,
            embedding_text_hash=None,
            error_detail=None,
        )
    )
    return int(result.rowcount or 0)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _paragraph_ids(content_json: dict) -> list[str]:
    """Return editor paragraph ids in document order (when present)."""
    result: list[str] = []

    def walk(nodes: object) -> None:
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("type") in {"paragraph", "heading", "listItem"}:
                attrs = node.get("attrs")
                if isinstance(attrs, dict) and isinstance(attrs.get("pid"), str):
                    result.append(attrs["pid"])
            walk(node.get("content"))

    walk(content_json.get("content") if isinstance(content_json, dict) else None)
    return result


def _chunk_paragraph_ids(chunk: TextChunk, paragraph_ids: list[str]) -> list[str]:
    values: list[str] = []
    for position in chunk.paragraph_positions:
        if 0 <= position < len(paragraph_ids):
            values.append(paragraph_ids[position])
    return list(dict.fromkeys(values))


async def replace_chapter_chunks(
    db: AsyncSession,
    *,
    chapter_id: str,
    project_id: str,
    body_rev: int,
    content_html: str,
    content_json: dict,
    embedding_provider: Optional[EmbeddingProvider] = None,
    max_chars: Optional[int] = None,
    overlap_chars: Optional[int] = None,
) -> list[ChapterChunk]:
    """Replace the searchable chunk set for one body revision.

    Previous revisions are retained as ``stale`` audit rows. The operation is
    idempotent for the same ``chapter_id/body_rev/chunk_index`` key and preserves
    an existing vector when the chunk content hash is unchanged.

    Embedding is optional so body saves remain a local transaction. Callers that
    provide a provider get synchronous embedding for the newly changed chunks;
    otherwise rows remain ``pending`` for a backfill worker.
    """
    if body_rev < 0:
        raise ValueError("body_rev must be non-negative")
    chapter = await db.scalar(
        select(Chapter.id).where(Chapter.id == chapter_id, Chapter.project_id == project_id)
    )
    if chapter is None:
        raise ValueError("chapter does not belong to project")

    await db.execute(
        update(ChapterChunk)
        .where(ChapterChunk.chapter_id == chapter_id)
        .where(ChapterChunk.status != "stale")
        .values(status="stale")
    )

    max_chars = settings.consistency_chunk_chars if max_chars is None else max_chars
    overlap_chars = settings.consistency_chunk_overlap_chars if overlap_chars is None else overlap_chars
    chunks = chunk_html(
        content_html,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
        max_chunks=settings.consistency_max_chunks,
    )
    paragraph_ids = _paragraph_ids(content_json)
    existing_rows = list(
        (
            await db.execute(
                select(ChapterChunk).where(
                    ChapterChunk.chapter_id == chapter_id,
                    ChapterChunk.body_rev == body_rev,
                )
            )
        )
        .scalars()
        .all()
    )
    existing = {row.chunk_index: row for row in existing_rows}
    output: list[ChapterChunk] = []
    to_embed: list[ChapterChunk] = []
    for chunk in chunks:
        digest = _content_hash(chunk.text)
        row = existing.get(chunk.index)
        ids = _chunk_paragraph_ids(chunk, paragraph_ids)
        if row is None:
            row = ChapterChunk(
                id=secrets.token_hex(16),
                project_id=project_id,
                chapter_id=chapter_id,
                body_rev=body_rev,
                chunk_index=chunk.index,
                paragraph_start=chunk.start_paragraph,
                paragraph_end=chunk.end_paragraph,
                paragraph_ids=ids,
                content_text=chunk.text,
                content_hash=digest,
                status="pending",
            )
            db.add(row)
        else:
            changed = row.content_hash != digest
            row.project_id = project_id
            row.paragraph_start = chunk.start_paragraph
            row.paragraph_end = chunk.end_paragraph
            row.paragraph_ids = ids
            row.content_text = chunk.text
            row.content_hash = digest
            if changed:
                row.embedding = None
                row.embedding_text_hash = None
            row.status = "ready" if row.embedding is not None and not changed else "pending"
            row.error_detail = None
        output.append(row)
        if row.status == "pending":
            to_embed.append(row)

    await db.flush()
    if embedding_provider and to_embed:
        vectors = await embedding_provider.embed_batch([row.content_text for row in to_embed])
        if len(vectors) != len(to_embed):
            raise RuntimeError("embedding provider returned an unexpected vector count")
        for row, vector in zip(to_embed, vectors):
            row.embedding = vector
            row.embedding_text_hash = row.content_hash
            row.status = "ready"
            row.error_detail = None
        await db.flush()
    return output


async def embed_pending_chapter_chunks(
    db: AsyncSession,
    provider: EmbeddingProvider,
    *,
    project_id: str,
    chapter_id: Optional[str] = None,
    body_rev: Optional[int] = None,
    batch_size: int = 32,
) -> int:
    """Backfill current chunks, optionally pinned to an outbox body revision."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    stmt = (
        select(ChapterChunk)
        .join(Chapter, Chapter.id == ChapterChunk.chapter_id)
        .join(ChapterBody, ChapterBody.chapter_id == ChapterChunk.chapter_id)
        .where(ChapterChunk.project_id == project_id)
        .where(Chapter.project_id == project_id)
        .where(Chapter.deleted_at.is_(None))
        .where(ChapterChunk.status.in_(("pending", "failed")))
        .where(ChapterChunk.embedding.is_(None))
        .where(ChapterChunk.body_rev == ChapterBody.rev)
        .order_by(ChapterChunk.chapter_id, ChapterChunk.chunk_index)
    )
    if chapter_id:
        stmt = stmt.where(ChapterChunk.chapter_id == chapter_id)
    if body_rev is not None:
        stmt = stmt.where(ChapterChunk.body_rev == body_rev)
    rows = list((await db.execute(stmt)).scalars().all())
    written = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        vectors = await provider.embed_batch([row.content_text for row in batch])
        if len(vectors) != len(batch):
            raise RuntimeError("embedding provider returned an unexpected vector count")
        for row, vector in zip(batch, vectors):
            row.embedding = vector
            row.embedding_text_hash = row.content_hash
            row.status = "ready"
            row.error_detail = None
            written += 1
    await db.flush()
    return written


async def mark_chapter_chunks_failed(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    body_rev: int,
    error: Exception,
) -> int:
    """Expose terminal embedding failure without mutating another revision."""
    result = await db.execute(
        update(ChapterChunk)
        .where(
            ChapterChunk.project_id == project_id,
            ChapterChunk.chapter_id == chapter_id,
            ChapterChunk.body_rev == body_rev,
            ChapterChunk.status.in_(("pending", "failed")),
        )
        .values(status="failed", error_detail=str(error)[:1000])
    )
    return int(result.rowcount or 0)


async def count_current_chapter_chunks(db: AsyncSession, *, chapter_id: str, body_rev: int) -> dict[str, int]:
    """Return index health for a chapter head without loading chunk text."""
    from sqlalchemy import func

    result = await db.execute(
        select(ChapterChunk.status, func.count(ChapterChunk.id))
        .where(ChapterChunk.chapter_id == chapter_id, ChapterChunk.body_rev == body_rev)
        .group_by(ChapterChunk.status)
    )
    return {str(status): int(count) for status, count in result.all()}


def current_chunk_query(*, project_id: str, chapter_ids: Optional[Iterable[str]] = None):
    """Build the shared current-revision filter used by retrieval and audits."""
    stmt = (
        select(ChapterChunk)
        .join(Chapter, Chapter.id == ChapterChunk.chapter_id)
        .join(ChapterBody, ChapterBody.chapter_id == ChapterChunk.chapter_id)
        .where(ChapterChunk.project_id == project_id)
        .where(Chapter.project_id == project_id)
        .where(Chapter.deleted_at.is_(None))
        .where(ChapterChunk.status == "ready")
        .where(ChapterChunk.embedding.is_not(None))
        .where(ChapterChunk.body_rev == ChapterBody.rev)
    )
    if chapter_ids is not None:
        ids = list(chapter_ids)
        stmt = stmt.where(ChapterChunk.chapter_id.in_(ids)) if ids else stmt.where(False)
    return stmt


__all__ = [
    "ProjectChunkIndexStatus",
    "current_chunk_states_by_chapter",
    "current_chunk_query",
    "embed_pending_chapter_chunks",
    "mark_chapter_chunks_failed",
    "mark_current_chapter_chunks_for_reindex",
    "project_body_revisions",
    "project_chunk_index_status",
    "count_current_chapter_chunks",
    "replace_chapter_chunks",
]
