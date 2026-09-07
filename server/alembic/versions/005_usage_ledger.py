"""Add a transactional usage ledger and monthly quota reset timestamp.

Revision ID: 005_usage_ledger
Revises: 004_auth_admin_rbac
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "005_usage_ledger"
down_revision: str | None = "004_auth_admin_rbac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("quota_resets_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("usage_logs", sa.Column("project_id", sa.String(length=32), nullable=True))
    op.add_column("usage_logs", sa.Column("run_id", sa.String(length=32), nullable=True))
    op.add_column("usage_logs", sa.Column("model", sa.String(length=100), nullable=True))
    op.add_column("usage_logs", sa.Column("prompt_tokens", sa.Integer(), server_default="0", nullable=False))
    op.add_column("usage_logs", sa.Column("cached_tokens", sa.Integer(), server_default="0", nullable=False))
    op.add_column("usage_logs", sa.Column("completion_tokens", sa.Integer(), server_default="0", nullable=False))
    op.add_column("usage_logs", sa.Column("reserved_credits", sa.Integer(), server_default="0", nullable=False))
    op.add_column("usage_logs", sa.Column("status", sa.String(length=20), server_default="completed", nullable=False))
    op.add_column(
        "usage_logs",
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("usage_logs", sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_usage_logs_project_id_projects", "usage_logs", "projects", ["project_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_usage_logs_run_id_generation_runs", "usage_logs", "generation_runs", ["run_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_usage_logs_project_id", "usage_logs", ["project_id"])
    op.create_index("ix_usage_logs_run_id", "usage_logs", ["run_id"], unique=True)
    op.create_index("ix_usage_logs_status", "usage_logs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_usage_logs_status", table_name="usage_logs")
    op.drop_index("ix_usage_logs_run_id", table_name="usage_logs")
    op.drop_index("ix_usage_logs_project_id", table_name="usage_logs")
    op.drop_constraint("fk_usage_logs_run_id_generation_runs", "usage_logs", type_="foreignkey")
    op.drop_constraint("fk_usage_logs_project_id_projects", "usage_logs", type_="foreignkey")
    for column in (
        "finalized_at",
        "detail",
        "status",
        "reserved_credits",
        "completion_tokens",
        "cached_tokens",
        "prompt_tokens",
        "model",
        "run_id",
        "project_id",
    ):
        op.drop_column("usage_logs", column)
    op.drop_column("users", "quota_resets_at")
