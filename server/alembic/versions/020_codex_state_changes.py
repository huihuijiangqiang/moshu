"""Add author-maintained Codex state changes.

Revision ID: 020_codex_state_changes
Revises: 019_chapter_pov
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "020_codex_state_changes"
down_revision: str | None = "019_chapter_pov"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "codex_state_changes",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("entry_id", sa.String(length=32), nullable=False),
        sa.Column("chapter_id", sa.String(length=32), nullable=False),
        sa.Column("state_key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("rev", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'archived')", name="ck_codex_state_change_status"
        ),
        sa.CheckConstraint("rev > 0", name="ck_codex_state_change_rev_positive"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["entry_id"], ["codex_entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_codex_state_changes_project_id"),
        "codex_state_changes",
        ["project_id"],
    )
    op.create_index(
        op.f("ix_codex_state_changes_entry_id"),
        "codex_state_changes",
        ["entry_id"],
    )
    op.create_index(
        op.f("ix_codex_state_changes_chapter_id"),
        "codex_state_changes",
        ["chapter_id"],
    )
    op.create_index(
        "ix_codex_state_change_history",
        "codex_state_changes",
        ["project_id", "entry_id", "status", "chapter_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_codex_state_change_history", table_name="codex_state_changes")
    op.drop_index(op.f("ix_codex_state_changes_chapter_id"), table_name="codex_state_changes")
    op.drop_index(op.f("ix_codex_state_changes_entry_id"), table_name="codex_state_changes")
    op.drop_index(op.f("ix_codex_state_changes_project_id"), table_name="codex_state_changes")
    op.drop_table("codex_state_changes")
