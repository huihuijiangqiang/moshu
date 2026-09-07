"""Persist deterministic relative-time normalization and dependency diagnostics.

Revision ID: 017_temporal_resolution
Revises: 016_platform_usage_ledger
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "017_temporal_resolution"
down_revision: str | None = "016_platform_usage_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "consistency_claims",
        sa.Column("temporal_resolution", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("consistency_claims", "temporal_resolution")
