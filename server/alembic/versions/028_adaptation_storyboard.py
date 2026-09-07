"""Add independent comic-drama adaptation storyboard tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "028_adaptation_storyboard"
down_revision: str | None = "027_chapter_temporal_anchor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    jsonb = postgresql.JSONB()
    op.create_table(
        "adaptations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project_id", sa.String(32), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("format", sa.String(30), nullable=False, server_default="comic_drama"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("aspect_ratio", sa.String(20), nullable=False, server_default="9:16"),
        sa.Column("style_profile", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_adaptations_project_id", "adaptations", ["project_id"])
    op.create_table(
        "adaptation_episodes",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("adaptation_id", sa.String(32), sa.ForeignKey("adaptations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("source_chapter_ids", jsonb, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("target_duration", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_adaptation_episodes_adaptation_id", "adaptation_episodes", ["adaptation_id"])
    op.create_table(
        "adaptation_scenes",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("episode_id", sa.String(32), sa.ForeignKey("adaptation_episodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(200), nullable=False, server_default=""),
        sa.Column("location_entry_id", sa.String(32), sa.ForeignKey("codex_entries.id", ondelete="SET NULL")),
        sa.Column("time_anchor", sa.String(100), nullable=False, server_default=""),
        sa.Column("character_entry_ids", jsonb, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_adaptation_scenes_episode_id", "adaptation_scenes", ["episode_id"])
    op.create_table(
        "adaptation_shots",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("scene_id", sa.String(32), sa.ForeignKey("adaptation_scenes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("shot_type", sa.String(30), nullable=False, server_default="medium"),
        sa.Column("camera", sa.String(100), nullable=False, server_default="static"),
        sa.Column("duration_target", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("action", sa.Text(), nullable=False, server_default=""),
        sa.Column("dialogue", sa.Text(), nullable=False, server_default=""),
        sa.Column("narration", sa.Text(), nullable=False, server_default=""),
        sa.Column("visual_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("reference_asset_ids", jsonb, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_adaptation_shots_scene_id", "adaptation_shots", ["scene_id"])
    op.create_table(
        "adaptation_visual_profiles",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("adaptation_id", sa.String(32), sa.ForeignKey("adaptations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("codex_entry_id", sa.String(32), sa.ForeignKey("codex_entries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("style", sa.String(100), nullable=False, server_default=""),
        sa.Column("appearance", sa.Text(), nullable=False, server_default=""),
        sa.Column("costume", sa.Text(), nullable=False, server_default=""),
        sa.Column("palette", jsonb, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("reference_asset_ids", jsonb, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_adaptation_visual_profiles_adaptation_id", "adaptation_visual_profiles", ["adaptation_id"])
    op.create_index("ix_adaptation_visual_profiles_codex_entry_id", "adaptation_visual_profiles", ["codex_entry_id"])


def downgrade() -> None:
    op.drop_table("adaptation_visual_profiles")
    op.drop_table("adaptation_shots")
    op.drop_table("adaptation_scenes")
    op.drop_table("adaptation_episodes")
    op.drop_table("adaptations")
