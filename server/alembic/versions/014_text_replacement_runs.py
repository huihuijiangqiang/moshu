"""Track atomic multi-chapter text replacements and their undo boundary.

Revision ID: 014_text_replacement_runs
Revises: 013_generation_drafts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "014_text_replacement_runs"
down_revision: str | None = "013_generation_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "text_replacement_runs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("replacement_text", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("case_sensitive", sa.Boolean(), nullable=False),
        sa.Column("total_matches", sa.Integer(), nullable=False),
        sa.Column("affected_chapters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('applied', 'undone')", name="ck_text_replacement_run_status"),
        sa.CheckConstraint("scope IN ('chapter', 'volume', 'project')", name="ck_text_replacement_run_scope"),
        sa.CheckConstraint("total_matches > 0", name="ck_text_replacement_run_matches_positive"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_text_replacement_runs_project_id"), "text_replacement_runs", ["project_id"])
    op.create_index(op.f("ix_text_replacement_runs_status"), "text_replacement_runs", ["status"])
    op.create_index(op.f("ix_text_replacement_runs_user_id"), "text_replacement_runs", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_text_replacement_runs_user_id"), table_name="text_replacement_runs")
    op.drop_index(op.f("ix_text_replacement_runs_status"), table_name="text_replacement_runs")
    op.drop_index(op.f("ix_text_replacement_runs_project_id"), table_name="text_replacement_runs")
    op.drop_table("text_replacement_runs")
