"""Separate broker redispatch attempts from embedding execution attempts.

Revision ID: 009_embedding_dispatch_attempts
Revises: 008_embedding_job_visibility
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "009_embedding_dispatch_attempts"
down_revision: str | None = "008_embedding_job_visibility"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "codex_embedding_jobs",
        sa.Column(
            "dispatch_attempts",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_codex_embedding_job_attempts_nonnegative",
        "codex_embedding_jobs",
        "attempts >= 0",
    )
    op.create_check_constraint(
        "ck_codex_embedding_job_dispatch_attempts_nonnegative",
        "codex_embedding_jobs",
        "dispatch_attempts >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_codex_embedding_job_dispatch_attempts_nonnegative",
        "codex_embedding_jobs",
        type_="check",
    )
    op.drop_constraint(
        "ck_codex_embedding_job_attempts_nonnegative",
        "codex_embedding_jobs",
        type_="check",
    )
    op.drop_column("codex_embedding_jobs", "dispatch_attempts")
