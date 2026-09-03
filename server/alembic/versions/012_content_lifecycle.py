"""Add recycle-bin state for volumes and chapters.

Revision ID: 012_content_lifecycle
Revises: 011_guard_issue_arbitration
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "012_content_lifecycle"
down_revision: str | None = "011_guard_issue_arbitration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("volumes", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("chapters", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_volumes_deleted_at", "volumes", ["deleted_at"])
    op.create_index("ix_chapters_deleted_at", "chapters", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_chapters_deleted_at", table_name="chapters")
    op.drop_index("ix_volumes_deleted_at", table_name="volumes")
    op.drop_column("chapters", "deleted_at")
    op.drop_column("volumes", "deleted_at")
