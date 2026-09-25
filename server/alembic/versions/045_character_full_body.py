"""Attach full-body character sheets to visual profiles and image jobs."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "045_character_full_body"
down_revision: str | None = "044_production_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "production_assets",
        sa.Column("visual_profile_id", sa.String(32), sa.ForeignKey("adaptation_visual_profiles.id", ondelete="SET NULL")),
    )
    op.create_index("ix_production_assets_visual_profile_id", "production_assets", ["visual_profile_id"])
    op.add_column(
        "production_jobs",
        sa.Column("visual_profile_id", sa.String(32), sa.ForeignKey("adaptation_visual_profiles.id", ondelete="SET NULL")),
    )
    op.create_index("ix_production_jobs_visual_profile_id", "production_jobs", ["visual_profile_id"])


def downgrade() -> None:
    op.drop_index("ix_production_jobs_visual_profile_id", table_name="production_jobs")
    op.drop_column("production_jobs", "visual_profile_id")
    op.drop_index("ix_production_assets_visual_profile_id", table_name="production_assets")
    op.drop_column("production_assets", "visual_profile_id")
