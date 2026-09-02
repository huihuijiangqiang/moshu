"""Persist Codex embedding backfill attempts and dead letters.

Revision ID: 008_embedding_job_visibility
Revises: 007_style_profiles
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "008_embedding_job_visibility"
down_revision: str | None = "007_style_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "codex_embedding_jobs",
        sa.Column(
            "project_id",
            sa.String(length=32),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("embedded_count", sa.Integer(), nullable=False),
        sa.Column("remaining_count", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exhausted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','running','retrying','succeeded','dead_letter')",
            name="ck_codex_embedding_job_status",
        ),
    )
    op.create_index("ix_codex_embedding_jobs_status", "codex_embedding_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_codex_embedding_jobs_status", table_name="codex_embedding_jobs")
    op.drop_table("codex_embedding_jobs")
