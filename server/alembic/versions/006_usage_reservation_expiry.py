"""Add expiry recovery for credit reservations.

Revision ID: 006_usage_reservation_expiry
Revises: 005_usage_ledger
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "006_usage_reservation_expiry"
down_revision: str | None = "005_usage_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "usage_logs",
        sa.Column("reservation_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_usage_logs_reservation_expires_at",
        "usage_logs",
        ["reservation_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_usage_logs_reservation_expires_at", table_name="usage_logs")
    op.drop_column("usage_logs", "reservation_expires_at")
