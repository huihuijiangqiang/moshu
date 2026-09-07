"""Add authentication and project-planning fields.

Revision ID: 003_product_workflows
Revises: 002_embedding_halfvec_2048
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "003_product_workflows"
down_revision: str | None = "002_embedding_halfvec_2048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.add_column("projects", sa.Column("inspiration", sa.Text(), nullable=True))
    op.add_column("projects", sa.Column("synopsis", sa.Text(), nullable=True))
    op.add_column(
        "projects",
        sa.Column(
            "story_settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "story_settings")
    op.drop_column("projects", "synopsis")
    op.drop_column("projects", "inspiration")
    op.drop_column("users", "password_hash")
