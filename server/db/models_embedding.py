"""Persistent visibility for Codex embedding backfill jobs."""

from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin

EMBEDDING_JOB_STATUSES = ("queued", "running", "retrying", "succeeded", "dead_letter")


class CodexEmbeddingJob(Base, TimestampMixin):
    __tablename__ = "codex_embedding_jobs"

    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    dispatch_attempts: Mapped[int] = mapped_column(Integer, default=0)
    embedded_count: Mapped[int] = mapped_column(Integer, default=0)
    remaining_count: Mapped[int] = mapped_column(Integer, default=0)
    task_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    exhausted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','retrying','succeeded','dead_letter')",
            name="ck_codex_embedding_job_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_codex_embedding_job_attempts_nonnegative"),
        CheckConstraint(
            "dispatch_attempts >= 0",
            name="ck_codex_embedding_job_dispatch_attempts_nonnegative",
        ),
        Index("ix_codex_embedding_jobs_status", "status"),
    )
