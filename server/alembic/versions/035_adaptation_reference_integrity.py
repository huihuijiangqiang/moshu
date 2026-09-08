"""Enforce one visual profile per Codex entry in an adaptation."""

from alembic import op


revision: str = "035_adaptation_refs"
down_revision: str | None = "034_chapter_chunks"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM adaptation_visual_profiles
                GROUP BY adaptation_id, codex_entry_id
                HAVING COUNT(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'duplicate adaptation visual profiles found; merge them before applying 035_adaptation_refs';
            END IF;
        END
        $$
        """
    )
    op.create_index(
        "uq_adaptation_visual_profile_entry",
        "adaptation_visual_profiles",
        ["adaptation_id", "codex_entry_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_adaptation_visual_profile_entry",
        table_name="adaptation_visual_profiles",
    )
