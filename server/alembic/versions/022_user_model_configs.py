"""Add encrypted user model configurations.

Revision ID: 022_user_model_configs
Revises: 021_chapter_reviews
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "022_user_model_configs"
down_revision: str | None = "021_chapter_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_model_configs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("provider_name", sa.String(length=100), nullable=False),
        sa.Column("base_url", sa.String(length=1000), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("api_key_ciphertext", sa.Text(), nullable=False),
        sa.Column("api_key_hint", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("last_test_status", sa.String(length=20), server_default="untested", nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_user_model_config_revision_positive"),
        sa.CheckConstraint(
            "last_test_status IN ('untested', 'ok', 'failed')",
            name="ck_user_model_config_test_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_user_model_configs_user_id"),
    )
    op.create_index(op.f("ix_user_model_configs_user_id"), "user_model_configs", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_user_model_configs_user_id"), table_name="user_model_configs")
    op.drop_table("user_model_configs")
