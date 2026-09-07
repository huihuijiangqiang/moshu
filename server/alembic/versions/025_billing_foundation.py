"""Add provider-neutral billing orders and purchased credit grants.

Revision ID: 025_billing_foundation
Revises: 024_project_daily_writing
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "025_billing_foundation"
down_revision: str | None = "024_project_daily_writing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("purchased_credits_remaining", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        "ck_users_purchased_credits_nonnegative",
        "users",
        "purchased_credits_remaining >= 0",
    )

    json_type = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "billing_products",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("plan", sa.String(length=50), nullable=True),
        sa.Column("currency", sa.String(length=3), server_default="CNY", nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("billing_interval", sa.String(length=20), server_default="one_time", nullable=False),
        sa.Column("provider_prices", json_type, nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("amount_minor >= 0", name="ck_billing_products_amount_nonnegative"),
        sa.CheckConstraint("credits > 0", name="ck_billing_products_credits_positive"),
        sa.CheckConstraint(
            "billing_interval IN ('one_time', 'month', 'year')",
            name="ck_billing_products_interval",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_billing_products_code", "billing_products", ["code"], unique=True)
    op.create_index("ix_billing_products_is_active", "billing_products", ["is_active"], unique=False)

    op.create_table(
        "billing_orders",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("product_id", sa.String(length=32), nullable=True),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("product_snapshot", json_type, nullable=False),
        sa.Column("provider_order_id", sa.String(length=128), nullable=True),
        sa.Column("provider_checkout_id", sa.String(length=128), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("amount_minor >= 0", name="ck_billing_orders_amount_nonnegative"),
        sa.CheckConstraint("credits > 0", name="ck_billing_orders_credits_positive"),
        sa.CheckConstraint(
            "status IN ('pending', 'paid', 'cancelled', 'failed', 'refunded', 'partially_refunded')",
            name="ck_billing_orders_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["billing_products.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_billing_orders_user_id", "billing_orders", ["user_id"], unique=False)
    op.create_index("ix_billing_orders_product_id", "billing_orders", ["product_id"], unique=False)
    op.create_index("ix_billing_orders_status", "billing_orders", ["status"], unique=False)
    op.create_index("ix_billing_orders_provider_order_id", "billing_orders", ["provider_order_id"], unique=False)
    op.create_index("uq_billing_orders_user_idempotency", "billing_orders", ["user_id", "idempotency_key"], unique=True)
    op.create_index("uq_billing_orders_provider_order", "billing_orders", ["provider", "provider_order_id"], unique=True)

    op.create_table(
        "credit_grants",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("order_id", sa.String(length=32), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("remaining_credits", sa.Integer(), nullable=False),
        sa.Column("metadata", json_type, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("credits > 0", name="ck_credit_grants_credits_positive"),
        sa.CheckConstraint("remaining_credits >= 0 AND remaining_credits <= credits", name="ck_credit_grants_remaining"),
        sa.CheckConstraint("kind IN ('purchase', 'manual', 'refund', 'reversal')", name="ck_credit_grants_kind"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["billing_orders.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_credit_grants_user_id", "credit_grants", ["user_id"], unique=False)
    op.create_index("ix_credit_grants_order_id", "credit_grants", ["order_id"], unique=False)
    op.create_index("ix_credit_grants_expires_at", "credit_grants", ["expires_at"], unique=False)

    op.create_table(
        "billing_webhook_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("provider_event_id", sa.String(length=160), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="received", nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('received', 'processed', 'failed')", name="ck_billing_webhook_status"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_billing_webhook_provider_event",
        "billing_webhook_events",
        ["provider", "provider_event_id"],
        unique=True,
    )
    op.create_index("ix_billing_webhook_events_status", "billing_webhook_events", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_billing_webhook_events_status", table_name="billing_webhook_events")
    op.drop_index("uq_billing_webhook_provider_event", table_name="billing_webhook_events")
    op.drop_table("billing_webhook_events")
    op.drop_index("ix_credit_grants_expires_at", table_name="credit_grants")
    op.drop_index("ix_credit_grants_order_id", table_name="credit_grants")
    op.drop_index("ix_credit_grants_user_id", table_name="credit_grants")
    op.drop_table("credit_grants")
    op.drop_index("uq_billing_orders_provider_order", table_name="billing_orders")
    op.drop_index("uq_billing_orders_user_idempotency", table_name="billing_orders")
    op.drop_index("ix_billing_orders_provider_order_id", table_name="billing_orders")
    op.drop_index("ix_billing_orders_status", table_name="billing_orders")
    op.drop_index("ix_billing_orders_product_id", table_name="billing_orders")
    op.drop_index("ix_billing_orders_user_id", table_name="billing_orders")
    op.drop_table("billing_orders")
    op.drop_index("ix_billing_products_is_active", table_name="billing_products")
    op.drop_index("ix_billing_products_code", table_name="billing_products")
    op.drop_table("billing_products")
    op.drop_constraint("ck_users_purchased_credits_nonnegative", "users", type_="check")
    op.drop_column("users", "purchased_credits_remaining")
