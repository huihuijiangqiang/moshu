"""Persistent planning state for resumable long-form generation.

Long novels are generated as ordered, independently retryable segments.  The
segment row is deliberately separate from ``GenerationDraft``: drafts are
author-facing candidates, while these rows are the execution ledger used to
resume a run without duplicating accepted prose.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin


class StoryArc(Base, TimestampMixin):
    __tablename__ = "story_arcs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(Text, default="", server_default="")
    start_chapter_idx: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    end_chapter_idx: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="planned", server_default="planned")
    objectives: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    constraints: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    __table_args__ = (
        CheckConstraint("status IN ('planned', 'active', 'completed', 'paused', 'archived')", name="ck_story_arc_status"),
        CheckConstraint("version > 0", name="ck_story_arc_version_positive"),
        CheckConstraint("start_chapter_idx IS NULL OR start_chapter_idx > 0", name="ck_story_arc_start_positive"),
        CheckConstraint("end_chapter_idx IS NULL OR end_chapter_idx >= start_chapter_idx", name="ck_story_arc_range"),
        Index("ix_story_arcs_project_status", "project_id", "status"),
    )


class PlotThread(Base, TimestampMixin):
    __tablename__ = "plot_threads"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    arc_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("story_arcs.id", ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    thread_type: Mapped[str] = mapped_column(String(30), default="main", server_default="main")
    priority: Mapped[int] = mapped_column(Integer, default=50, server_default="50")
    status: Mapped[str] = mapped_column(String(20), default="open", server_default="open")
    objective: Mapped[str] = mapped_column(Text, default="", server_default="")
    stakes: Mapped[str] = mapped_column(Text, default="", server_default="")
    resolution: Mapped[str] = mapped_column(Text, default="", server_default="")
    state: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    __table_args__ = (
        CheckConstraint("thread_type IN ('main', 'subplot', 'mystery', 'political', 'romance', 'character')", name="ck_plot_thread_type"),
        CheckConstraint("status IN ('open', 'resolved', 'dormant', 'abandoned')", name="ck_plot_thread_status"),
        CheckConstraint("priority BETWEEN 0 AND 100", name="ck_plot_thread_priority"),
        Index("ix_plot_threads_project_priority", "project_id", "priority"),
    )


class PlotBeat(Base, TimestampMixin):
    __tablename__ = "plot_beats"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    thread_id: Mapped[str] = mapped_column(String(32), ForeignKey("plot_threads.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True, index=True)
    beat_order: Mapped[int] = mapped_column(Integer)
    beat_type: Mapped[str] = mapped_column(String(30), default="turn", server_default="turn")
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    required_facts: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    actor_entry_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="planned", server_default="planned")
    due_chapter_idx: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("thread_id", "beat_order", name="uq_plot_beat_thread_order"),
        CheckConstraint("beat_order > 0", name="ck_plot_beat_order_positive"),
        CheckConstraint("status IN ('planned', 'ready', 'covered', 'skipped')", name="ck_plot_beat_status"),
        Index("ix_plot_beats_project_chapter", "project_id", "chapter_id"),
    )


class GenerationSegment(Base, TimestampMixin):
    __tablename__ = "generation_segments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[str] = mapped_column(String(32), ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("generation_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    segment_index: Mapped[int] = mapped_column(Integer)
    target_words: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    prompt_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    context_manifest: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    content_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    generated_words: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    __table_args__ = (
        UniqueConstraint("chapter_id", "segment_index", name="uq_generation_segment_chapter_index"),
        CheckConstraint("segment_index >= 0", name="ck_generation_segment_index_nonnegative"),
        CheckConstraint("target_words BETWEEN 100 AND 32000", name="ck_generation_segment_target_words"),
        CheckConstraint("status IN ('pending', 'running', 'ready', 'failed', 'accepted', 'skipped')", name="ck_generation_segment_status"),
        CheckConstraint("generated_words >= 0", name="ck_generation_segment_generated_words"),
        Index("ix_generation_segments_chapter_status", "chapter_id", "status", "segment_index"),
    )


__all__ = ["StoryArc", "PlotThread", "PlotBeat", "GenerationSegment"]
