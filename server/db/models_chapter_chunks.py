"""Versioned semantic chunks extracted from chapter bodies."""

from typing import Optional

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin


CHAPTER_CHUNK_STATUSES = ("pending", "ready", "stale", "failed")


class ChapterChunk(Base, TimestampMixin):
    """A searchable paragraph chunk tied to one immutable body revision.

    Rows are retained when a chapter changes so retrieval/audit history remains
    traceable. Retrieval always joins the current ChapterBody revision and only
    accepts ``ready`` rows, which makes stale vectors impossible to recall.
    """

    __tablename__ = "chapter_chunks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    chapter_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chapters.id", ondelete="CASCADE"), index=True
    )
    body_rev: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    paragraph_start: Mapped[int] = mapped_column(Integer, nullable=False)
    paragraph_end: Mapped[int] = mapped_column(Integer, nullable=False)
    paragraph_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[Optional[list[float]]] = mapped_column(HALFVEC(2048), nullable=True)
    embedding_text_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "chapter_id", "body_rev", "chunk_index", name="uq_chapter_chunk_revision_index"
        ),
        Index("ix_chapter_chunks_current_lookup", "project_id", "chapter_id", "body_rev", "status"),
        Index(
            "ix_chapter_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "halfvec_cosine_ops"},
        ),
        CheckConstraint("body_rev >= 0", name="ck_chapter_chunk_body_rev_nonnegative"),
        CheckConstraint("chunk_index >= 0", name="ck_chapter_chunk_index_nonnegative"),
        CheckConstraint("paragraph_start >= 0 AND paragraph_end >= paragraph_start", name="ck_chapter_chunk_paragraph_range"),
        CheckConstraint(
            "status IN ('pending', 'ready', 'stale', 'failed')",
            name="ck_chapter_chunk_status",
        ),
    )


__all__ = ["CHAPTER_CHUNK_STATUSES", "ChapterChunk"]
