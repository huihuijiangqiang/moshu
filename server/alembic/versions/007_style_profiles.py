"""Make style profiles usable and project bindings referentially safe.

Revision ID: 007_style_profiles
Revises: 006_usage_reservation_expiry
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "007_style_profiles"
down_revision: str | None = "006_usage_reservation_expiry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "style_profiles",
        sa.Column("sample_text", sa.Text(), server_default="", nullable=False),
    )
    op.add_column("style_profiles", sa.Column("error_detail", sa.Text(), nullable=True))
    op.add_column(
        "style_profiles",
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        WITH ranked AS (
          SELECT id, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY updated_at DESC, id) AS rn
          FROM style_profiles WHERE is_default = TRUE
        )
        UPDATE style_profiles SET is_default = FALSE
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
        """
    )
    op.create_index(
        "uq_style_profiles_default_user",
        "style_profiles",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.execute(
        """
        UPDATE projects SET style_profile_id = NULL
        WHERE style_profile_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM style_profiles WHERE style_profiles.id = projects.style_profile_id
          )
        """
    )
    op.create_foreign_key(
        "fk_projects_style_profile_id",
        "projects",
        "style_profiles",
        ["style_profile_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_projects_style_profile_id", "projects", type_="foreignkey")
    op.drop_index("uq_style_profiles_default_user", table_name="style_profiles")
    op.drop_column("style_profiles", "extracted_at")
    op.drop_column("style_profiles", "error_detail")
    op.drop_column("style_profiles", "sample_text")
