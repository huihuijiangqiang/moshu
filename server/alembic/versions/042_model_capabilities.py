"""Persist model-provider capability probe results."""

from alembic import op
import sqlalchemy as sa

revision = "042_model_capabilities"
down_revision = "041_story_hook_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_model_configs",
        sa.Column("capabilities", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("user_model_configs", "capabilities")
