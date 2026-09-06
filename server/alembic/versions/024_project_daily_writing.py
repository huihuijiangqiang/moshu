"""Persist daily net-positive writing progress.

Revision ID: 024_project_daily_writing
Revises: 023_project_notes
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "024_project_daily_writing"
down_revision: str | None = "023_project_notes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_daily_writing",
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("chapter_id", sa.String(length=32), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("words_added", sa.Integer(), server_default="0", nullable=False),
        sa.Column("saves", sa.Integer(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("words_added >= 0", name="ck_project_daily_writing_words_nonnegative"),
        sa.CheckConstraint("saves >= 0", name="ck_project_daily_writing_saves_nonnegative"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "chapter_id", "day"),
    )
    op.create_index(
        "ix_project_daily_writing_project_day",
        "project_daily_writing",
        ["project_id", "day"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_project_daily_writing_project_day", table_name="project_daily_writing")
    op.drop_table("project_daily_writing")
