"""Add story planning and resumable generation segment ledgers."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "038_long_generation_planning"
down_revision: str | None = "037_model_context_budgets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb(name: str, default: str = "'[]'") -> sa.Column:
    return sa.Column(name, postgresql.JSONB(), nullable=False, server_default=sa.text(default))


def upgrade() -> None:
    op.create_table(
        "story_arcs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project_id", sa.String(32), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False, server_default=""),
        sa.Column("start_chapter_idx", sa.Integer()),
        sa.Column("end_chapter_idx", sa.Integer()),
        sa.Column("status", sa.String(20), nullable=False, server_default="planned"),
        _jsonb("objectives"), _jsonb("constraints"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('planned', 'active', 'completed', 'paused', 'archived')", name="ck_story_arc_status"),
        sa.CheckConstraint("version > 0", name="ck_story_arc_version_positive"),
        sa.CheckConstraint("start_chapter_idx IS NULL OR start_chapter_idx > 0", name="ck_story_arc_start_positive"),
        sa.CheckConstraint("end_chapter_idx IS NULL OR start_chapter_idx IS NULL OR end_chapter_idx >= start_chapter_idx", name="ck_story_arc_range"),
    )
    op.create_index("ix_story_arcs_project_status", "story_arcs", ["project_id", "status"])
    op.create_index("ix_story_arcs_project_id", "story_arcs", ["project_id"])

    op.create_table(
        "plot_threads",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project_id", sa.String(32), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("arc_id", sa.String(32), sa.ForeignKey("story_arcs.id", ondelete="SET NULL")),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("thread_type", sa.String(30), nullable=False, server_default="main"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("objective", sa.Text(), nullable=False, server_default=""),
        sa.Column("stakes", sa.Text(), nullable=False, server_default=""),
        sa.Column("resolution", sa.Text(), nullable=False, server_default=""),
        _jsonb("state", "'{}'"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("thread_type IN ('main', 'subplot', 'mystery', 'political', 'romance', 'character')", name="ck_plot_thread_type"),
        sa.CheckConstraint("status IN ('open', 'resolved', 'dormant', 'abandoned')", name="ck_plot_thread_status"),
        sa.CheckConstraint("priority BETWEEN 0 AND 100", name="ck_plot_thread_priority"),
    )
    op.create_index("ix_plot_threads_project_priority", "plot_threads", ["project_id", "priority"])
    op.create_index("ix_plot_threads_project_id", "plot_threads", ["project_id"])
    op.create_index("ix_plot_threads_arc_id", "plot_threads", ["arc_id"])

    op.create_table(
        "plot_beats",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project_id", sa.String(32), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("thread_id", sa.String(32), sa.ForeignKey("plot_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_id", sa.String(32), sa.ForeignKey("chapters.id", ondelete="SET NULL")),
        sa.Column("beat_order", sa.Integer(), nullable=False),
        sa.Column("beat_type", sa.String(30), nullable=False, server_default="turn"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        _jsonb("required_facts"), _jsonb("actor_entry_ids"),
        sa.Column("status", sa.String(20), nullable=False, server_default="planned"),
        sa.Column("due_chapter_idx", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("thread_id", "beat_order", name="uq_plot_beat_thread_order"),
        sa.CheckConstraint("beat_order > 0", name="ck_plot_beat_order_positive"),
        sa.CheckConstraint("status IN ('planned', 'ready', 'covered', 'skipped')", name="ck_plot_beat_status"),
    )
    op.create_index("ix_plot_beats_project_chapter", "plot_beats", ["project_id", "chapter_id"])
    op.create_index("ix_plot_beats_project_id", "plot_beats", ["project_id"])
    op.create_index("ix_plot_beats_thread_id", "plot_beats", ["thread_id"])
    op.create_index("ix_plot_beats_chapter_id", "plot_beats", ["chapter_id"])

    op.create_table(
        "generation_segments",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project_id", sa.String(32), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_id", sa.String(32), sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("generation_runs.id", ondelete="SET NULL")),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("target_words", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("prompt_hash", sa.String(64)),
        _jsonb("context_manifest", "'{}'"),
        sa.Column("content_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("generated_words", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("error_code", sa.String(100)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("chapter_id", "segment_index", name="uq_generation_segment_chapter_index"),
        sa.CheckConstraint("segment_index >= 0", name="ck_generation_segment_index_nonnegative"),
        sa.CheckConstraint("target_words BETWEEN 100 AND 32000", name="ck_generation_segment_target_words"),
        sa.CheckConstraint("status IN ('pending', 'running', 'ready', 'failed', 'accepted', 'skipped')", name="ck_generation_segment_status"),
        sa.CheckConstraint("generated_words >= 0", name="ck_generation_segment_generated_words"),
    )
    op.create_index("ix_generation_segments_chapter_status", "generation_segments", ["chapter_id", "status", "segment_index"])
    op.create_index("ix_generation_segments_project_id", "generation_segments", ["project_id"])
    op.create_index("ix_generation_segments_chapter_id", "generation_segments", ["chapter_id"])
    op.create_index("ix_generation_segments_run_id", "generation_segments", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_generation_segments_run_id", table_name="generation_segments", if_exists=True)
    op.drop_index("ix_generation_segments_chapter_id", table_name="generation_segments", if_exists=True)
    op.drop_index("ix_generation_segments_project_id", table_name="generation_segments", if_exists=True)
    op.drop_index("ix_generation_segments_chapter_status", table_name="generation_segments", if_exists=True)
    op.drop_table("generation_segments")
    op.drop_index("ix_plot_beats_chapter_id", table_name="plot_beats", if_exists=True)
    op.drop_index("ix_plot_beats_thread_id", table_name="plot_beats", if_exists=True)
    op.drop_index("ix_plot_beats_project_id", table_name="plot_beats", if_exists=True)
    op.drop_index("ix_plot_beats_project_chapter", table_name="plot_beats", if_exists=True)
    op.drop_table("plot_beats")
    op.drop_index("ix_plot_threads_arc_id", table_name="plot_threads", if_exists=True)
    op.drop_index("ix_plot_threads_project_id", table_name="plot_threads", if_exists=True)
    op.drop_index("ix_plot_threads_project_priority", table_name="plot_threads", if_exists=True)
    op.drop_table("plot_threads")
    op.drop_index("ix_story_arcs_project_id", table_name="story_arcs", if_exists=True)
    op.drop_index("ix_story_arcs_project_status", table_name="story_arcs", if_exists=True)
    op.drop_table("story_arcs")
