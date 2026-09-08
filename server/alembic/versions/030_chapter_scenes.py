"""Add revisioned scene cards for chapter planning."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "030_chapter_scenes"
down_revision: str | None = "029_platform_positioning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chapter_scenes",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("chapter_id", sa.String(32), sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("pov_entry_id", sa.String(32), sa.ForeignKey("codex_entries.id", ondelete="SET NULL"), nullable=True),
        sa.Column("location_entry_id", sa.String(32), sa.ForeignKey("codex_entries.id", ondelete="SET NULL"), nullable=True),
        sa.Column("goal", sa.Text(), nullable=False, server_default=""),
        sa.Column("obstacle", sa.Text(), nullable=False, server_default=""),
        sa.Column("turn", sa.Text(), nullable=False, server_default=""),
        sa.Column("info_gain", sa.Text(), nullable=False, server_default=""),
        sa.Column("emotion_shift", sa.Text(), nullable=False, server_default=""),
        sa.Column("hook", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(24), nullable=False, server_default="planning"),
        sa.Column("rev", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("outline_rev", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("body_rev", sa.Integer(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("chapter_id", "order", name="uq_chapter_scene_order"),
        sa.CheckConstraint('"order" > 0', name="ck_chapter_scene_order_positive"),
        sa.CheckConstraint("rev > 0", name="ck_chapter_scene_rev_positive"),
        sa.CheckConstraint("outline_rev >= 0", name="ck_chapter_scene_outline_rev_nonnegative"),
        sa.CheckConstraint("body_rev IS NULL OR body_rev >= 0", name="ck_chapter_scene_body_rev_nonnegative"),
        sa.CheckConstraint(
            "status IN ('planning', 'ready', 'written', 'needs_revision', 'archived')",
            name="ck_chapter_scene_status",
        ),
    )
    op.create_index("ix_chapter_scenes_chapter_id", "chapter_scenes", ["chapter_id"])


def downgrade() -> None:
    op.drop_table("chapter_scenes")
