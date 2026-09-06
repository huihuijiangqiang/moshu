"""Add author-managed chapter point-of-view metadata.

Revision ID: 019_chapter_pov
Revises: 018_timeline_entries
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "019_chapter_pov"
down_revision: str | None = "018_timeline_entries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chapters",
        sa.Column("pov_entry_id", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "chapters",
        sa.Column("pov_revision", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_foreign_key(
        "fk_chapters_pov_entry_id_codex_entries",
        "chapters",
        "codex_entries",
        ["pov_entry_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_chapter_pov_revision_nonnegative",
        "chapters",
        "pov_revision >= 0",
    )
    op.create_index(op.f("ix_chapters_pov_entry_id"), "chapters", ["pov_entry_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_chapters_pov_entry_id"), table_name="chapters")
    op.drop_constraint("ck_chapter_pov_revision_nonnegative", "chapters", type_="check")
    op.drop_constraint(
        "fk_chapters_pov_entry_id_codex_entries", "chapters", type_="foreignkey"
    )
    op.drop_column("chapters", "pov_revision")
    op.drop_column("chapters", "pov_entry_id")
