"""Persistent naturalization review runs and paragraph-level candidates."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin


class NaturalizationRun(Base, TimestampMixin):
    """One explainable naturalization scan against an immutable body revision."""

    __tablename__ = "naturalization_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[str] = mapped_column(String(32), ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    source_body_rev: Mapped[int] = mapped_column(Integer)
    source_content_hash: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(20), default="chapter")
    mode: Mapped[str] = mapped_column(String(20), default="rules")
    status: Mapped[str] = mapped_column(String(20), default="ready", index=True)
    style_profile_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(40), default="naturalize-rules-v1")
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    finding_count: Mapped[int] = mapped_column(Integer, default=0)
    accepted_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        CheckConstraint("scope IN ('chapter', 'selection')", name="ck_naturalization_run_scope"),
        CheckConstraint("mode IN ('rules', 'assisted')", name="ck_naturalization_run_mode"),
        CheckConstraint(
            "status IN ('scanning', 'ready', 'stale', 'failed', 'completed')",
            name="ck_naturalization_run_status",
        ),
        CheckConstraint("source_body_rev >= 0", name="ck_naturalization_run_body_rev_nonnegative"),
        Index("ix_naturalization_runs_chapter_created", "chapter_id", "created_at"),
    )


class NaturalizationFinding(Base, TimestampMixin):
    """A candidate replacement anchored to one paragraph and source revision."""

    __tablename__ = "naturalization_findings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("naturalization_runs.id", ondelete="CASCADE"), index=True)
    paragraph_id: Mapped[str] = mapped_column(String(120), index=True)
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    original_text: Mapped[str] = mapped_column(Text)
    candidate_text: Mapped[str] = mapped_column(Text)
    source_text_hash: Mapped[str] = mapped_column(String(64))
    rule_ids: Mapped[list] = mapped_column(JSONB, default=list)
    reasons: Mapped[list] = mapped_column(JSONB, default=list)
    locked_facts: Mapped[dict] = mapped_column(JSONB, default=dict)
    validation: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        CheckConstraint('start >= 0 AND "end" > start', name="ck_naturalization_finding_range"),
        CheckConstraint("revision > 0", name="ck_naturalization_finding_revision_positive"),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected', 'stale')",
            name="ck_naturalization_finding_status",
        ),
        Index("ix_naturalization_findings_run_status", "run_id", "status"),
    )
