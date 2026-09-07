"""Persist AI draft candidates outside canonical chapter bodies.

Revision ID: 013_generation_drafts
Revises: 012_content_lifecycle
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "013_generation_drafts"
down_revision: str | None = "012_content_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "generation_drafts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=True),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("chapter_id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("generated_words", sa.Integer(), nullable=False),
        sa.Column("request_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("kind IN ('chapter', 'inline')", name="ck_generation_draft_kind"),
        sa.CheckConstraint(
            "status IN ('streaming', 'ready', 'failed', 'accepted', 'rejected')",
            name="ck_generation_draft_status",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["generation_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index(op.f("ix_generation_drafts_user_id"), "generation_drafts", ["user_id"])
    op.create_index(op.f("ix_generation_drafts_project_id"), "generation_drafts", ["project_id"])
    op.create_index(op.f("ix_generation_drafts_chapter_id"), "generation_drafts", ["chapter_id"])
    op.create_index(op.f("ix_generation_drafts_status"), "generation_drafts", ["status"])
    op.create_index(
        "ix_generation_drafts_chapter_status_created",
        "generation_drafts",
        ["chapter_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_generation_drafts_chapter_status_created", table_name="generation_drafts")
    op.drop_index(op.f("ix_generation_drafts_status"), table_name="generation_drafts")
    op.drop_index(op.f("ix_generation_drafts_chapter_id"), table_name="generation_drafts")
    op.drop_index(op.f("ix_generation_drafts_project_id"), table_name="generation_drafts")
    op.drop_index(op.f("ix_generation_drafts_user_id"), table_name="generation_drafts")
    op.drop_table("generation_drafts")
