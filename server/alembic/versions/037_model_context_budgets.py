"""Add validated context budgets to user model configurations."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "037_model_context_budgets"
down_revision: str | None = "036_password_reset_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_model_configs",
        sa.Column(
            "context_window_tokens",
            sa.Integer(),
            nullable=False,
            server_default="32768",
        ),
    )
    op.add_column(
        "user_model_configs",
        sa.Column(
            "max_output_tokens",
            sa.Integer(),
            nullable=False,
            server_default="4096",
        ),
    )
    op.add_column(
        "user_model_configs",
        sa.Column(
            "context_safety_margin_tokens",
            sa.Integer(),
            nullable=False,
            server_default="2048",
        ),
    )
    op.create_check_constraint(
        "ck_user_model_config_context_window",
        "user_model_configs",
        "context_window_tokens BETWEEN 4096 AND 2000000",
    )
    op.create_check_constraint(
        "ck_user_model_config_max_output",
        "user_model_configs",
        "max_output_tokens BETWEEN 256 AND 131072",
    )
    op.create_check_constraint(
        "ck_user_model_config_context_margin",
        "user_model_configs",
        "context_safety_margin_tokens BETWEEN 256 AND 262144",
    )
    op.create_check_constraint(
        "ck_user_model_config_input_budget",
        "user_model_configs",
        "context_window_tokens - max_output_tokens - context_safety_margin_tokens >= 1024",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_user_model_config_input_budget",
        "user_model_configs",
        type_="check",
    )
    op.drop_constraint(
        "ck_user_model_config_context_margin",
        "user_model_configs",
        type_="check",
    )
    op.drop_constraint(
        "ck_user_model_config_max_output",
        "user_model_configs",
        type_="check",
    )
    op.drop_constraint(
        "ck_user_model_config_context_window",
        "user_model_configs",
        type_="check",
    )
    op.drop_column("user_model_configs", "context_safety_margin_tokens")
    op.drop_column("user_model_configs", "max_output_tokens")
    op.drop_column("user_model_configs", "context_window_tokens")
