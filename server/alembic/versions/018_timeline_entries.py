"""Add author-managed story timeline entries.

Revision ID: 018_timeline_entries
Revises: 017_temporal_resolution
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "018_timeline_entries"
down_revision: str | None = "017_temporal_resolution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "timeline_entries",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("chapter_id", sa.String(length=32), nullable=True),
        sa.Column("timeline_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("time_text", sa.String(length=200), nullable=True),
        sa.Column("story_order", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("time_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("time_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("rev", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'archived')", name="ck_timeline_entry_status"
        ),
        sa.CheckConstraint("rev > 0", name="ck_timeline_entry_rev_positive"),
        sa.CheckConstraint(
            "time_end IS NULL OR time_start IS NOT NULL",
            name="ck_timeline_entry_range_start",
        ),
        sa.CheckConstraint(
            "time_end IS NULL OR time_end >= time_start",
            name="ck_timeline_entry_range_order",
        ),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_timeline_entries_project_id"), "timeline_entries", ["project_id"])
    op.create_index(op.f("ix_timeline_entries_chapter_id"), "timeline_entries", ["chapter_id"])
    op.create_index(op.f("ix_timeline_entries_timeline_id"), "timeline_entries", ["timeline_id"])
    op.create_index(
        "ix_timeline_entry_lane_order",
        "timeline_entries",
        ["project_id", "timeline_id", "story_order"],
    )


def downgrade() -> None:
    op.drop_index("ix_timeline_entry_lane_order", table_name="timeline_entries")
    op.drop_index(op.f("ix_timeline_entries_timeline_id"), table_name="timeline_entries")
    op.drop_index(op.f("ix_timeline_entries_chapter_id"), table_name="timeline_entries")
    op.drop_index(op.f("ix_timeline_entries_project_id"), table_name="timeline_entries")
    op.drop_table("timeline_entries")
