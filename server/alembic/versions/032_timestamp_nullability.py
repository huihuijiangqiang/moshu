"""Align workflow table timestamps with the ORM non-null contract."""

from collections.abc import Sequence

from alembic import op


revision: str = "032_timestamp_nullability"
down_revision: str | None = "031_naturalization_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TIMESTAMP_COLUMNS = {
    "adaptations": ("created_at", "updated_at"),
    "adaptation_episodes": ("created_at", "updated_at"),
    "adaptation_scenes": ("created_at", "updated_at"),
    "adaptation_shots": ("created_at", "updated_at"),
    "adaptation_visual_profiles": ("created_at", "updated_at"),
    "project_positionings": ("created_at", "updated_at"),
    "project_positioning_revisions": ("created_at",),
    "chapter_scenes": ("created_at", "updated_at"),
    "naturalization_runs": ("created_at", "updated_at"),
    "naturalization_findings": ("created_at", "updated_at"),
}


def upgrade() -> None:
    for table, columns in TIMESTAMP_COLUMNS.items():
        for column in columns:
            op.alter_column(table, column, nullable=False)


def downgrade() -> None:
    for table, columns in reversed(TIMESTAMP_COLUMNS.items()):
        for column in columns:
            op.alter_column(table, column, nullable=True)
