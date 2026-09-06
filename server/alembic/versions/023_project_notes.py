"""Add private project inspiration notes.

Revision ID: 023_project_notes
Revises: 022_user_model_configs
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "023_project_notes"
down_revision: str | None = "022_user_model_configs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_notes",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("chapter_id", sa.String(length=32), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_project_notes_project_id"), "project_notes", ["project_id"], unique=False)
    op.create_index(op.f("ix_project_notes_user_id"), "project_notes", ["user_id"], unique=False)
    op.create_index(op.f("ix_project_notes_chapter_id"), "project_notes", ["chapter_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_project_notes_chapter_id"), table_name="project_notes")
    op.drop_index(op.f("ix_project_notes_user_id"), table_name="project_notes")
    op.drop_index(op.f("ix_project_notes_project_id"), table_name="project_notes")
    op.drop_table("project_notes")
