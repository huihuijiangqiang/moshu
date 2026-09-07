"""Track platform model calls in the unified usage ledger.

Revision ID: 016_platform_usage_ledger
Revises: 015_foreshadow_lifecycle
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "016_platform_usage_ledger"
down_revision: str | None = "015_foreshadow_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("usage_logs", sa.Column("platform_event_id", sa.String(length=64), nullable=True))
    op.add_column(
        "usage_logs",
        sa.Column("provider_requests", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column(
        "usage_logs",
        sa.Column("usage_estimated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_check_constraint(
        "ck_usage_logs_provider_requests_positive",
        "usage_logs",
        "provider_requests > 0",
    )
    op.create_index(
        "uq_usage_logs_platform_event_id",
        "usage_logs",
        ["platform_event_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_usage_logs_platform_event_id", table_name="usage_logs")
    op.drop_constraint(
        "ck_usage_logs_provider_requests_positive", "usage_logs", type_="check"
    )
    op.drop_column("usage_logs", "usage_estimated")
    op.drop_column("usage_logs", "provider_requests")
    op.drop_column("usage_logs", "platform_event_id")
