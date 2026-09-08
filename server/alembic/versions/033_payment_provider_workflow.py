"""Complete provider payment, refund, and reconciliation state.

Revision ID: 033_payment_provider_workflow
Revises: 032_timestamp_nullability
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "033_payment_provider_workflow"
down_revision: str | None = "032_timestamp_nullability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("billing_orders", sa.Column("provider_refund_id", sa.String(length=128), nullable=True))
    op.add_column(
        "billing_orders",
        sa.Column("refunded_amount_minor", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("billing_orders", sa.Column("provider_synced_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint("ck_billing_orders_status", "billing_orders", type_="check")
    op.create_check_constraint(
        "ck_billing_orders_status",
        "billing_orders",
        "status IN ('pending', 'paid', 'cancelled', 'failed', 'refund_pending', 'refunded', 'partially_refunded')",
    )
    op.create_check_constraint(
        "ck_billing_orders_refunded_amount",
        "billing_orders",
        "refunded_amount_minor >= 0 AND refunded_amount_minor <= amount_minor",
    )


def downgrade() -> None:
    op.drop_constraint("ck_billing_orders_refunded_amount", "billing_orders", type_="check")
    op.drop_constraint("ck_billing_orders_status", "billing_orders", type_="check")
    op.create_check_constraint(
        "ck_billing_orders_status",
        "billing_orders",
        "status IN ('pending', 'paid', 'cancelled', 'failed', 'refunded', 'partially_refunded')",
    )
    op.drop_column("billing_orders", "provider_synced_at")
    op.drop_column("billing_orders", "refunded_amount_minor")
    op.drop_column("billing_orders", "provider_refund_id")
