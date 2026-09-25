"""Add private comic-drama production assets."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "043_production_assets"
down_revision: str | None = "042_model_capabilities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "production_assets",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("adaptation_id", sa.String(32), sa.ForeignKey("adaptations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("episode_id", sa.String(32), sa.ForeignKey("adaptation_episodes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("shot_id", sa.String(32), sa.ForeignKey("adaptation_shots.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.String(30), nullable=False, server_default="image"),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(32), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("storage_key", name="uq_production_assets_storage_key"),
    )
    op.create_index("ix_production_assets_adaptation_id", "production_assets", ["adaptation_id"])
    op.create_index("ix_production_assets_episode_id", "production_assets", ["episode_id"])
    op.create_index("ix_production_assets_shot_id", "production_assets", ["shot_id"])
    op.create_index("ix_production_assets_sha256", "production_assets", ["sha256"])


def downgrade() -> None:
    op.drop_table("production_assets")
