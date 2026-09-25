"""Add durable comic-drama image generation jobs."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "044_production_jobs"
down_revision: str | None = "043_production_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "production_jobs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("adaptation_id", sa.String(32), sa.ForeignKey("adaptations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("episode_id", sa.String(32), sa.ForeignKey("adaptation_episodes.id", ondelete="SET NULL")),
        sa.Column("shot_id", sa.String(32), sa.ForeignKey("adaptation_shots.id", ondelete="SET NULL")),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("client_request_id", sa.String(100), nullable=False),
        sa.Column("usage_log_id", sa.BigInteger(), sa.ForeignKey("usage_logs.id", ondelete="SET NULL")),
        sa.Column("asset_id", sa.String(32), sa.ForeignKey("production_assets.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("prompt_sha256", sa.String(64), nullable=False),
        sa.Column("profile_versions", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("credits > 0", name="ck_production_jobs_credits_positive"),
    )
    op.create_index("ix_production_jobs_adaptation_shot", "production_jobs", ["adaptation_id", "shot_id"])
    op.create_index("ix_production_jobs_status_created", "production_jobs", ["status", "created_at"])
    op.create_index("uq_production_jobs_user_request", "production_jobs", ["user_id", "client_request_id"], unique=True)


def downgrade() -> None:
    op.drop_table("production_jobs")
