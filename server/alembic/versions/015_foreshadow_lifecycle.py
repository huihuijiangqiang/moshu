"""Make foreshadow lifecycle canonical and backfill legacy codex fields.

Revision ID: 015_foreshadow_lifecycle
Revises: 014_text_replacement_runs
"""

from collections.abc import Sequence

from alembic import op

revision: str = "015_foreshadow_lifecycle"
down_revision: str | None = "014_text_replacement_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Deployment contract: the Compose API/worker services depend on the one-shot
    # migration service completing, so Codex writes are stopped during this backfill.
    # ON CONFLICT remains as recovery protection for installations that already
    # populated the lifecycle table before adopting this canonical constraint.
    # The table existed since revision 001 but had no API owner and no uniqueness
    # invariant. Keep one deterministic row before making entry_id canonical.
    op.execute(
        """
        DELETE FROM foreshadows older
        USING foreshadows newer
        WHERE older.entry_id = newer.entry_id AND older.ctid < newer.ctid
        """
    )
    op.create_index("uq_foreshadow_entry", "foreshadows", ["entry_id"], unique=True)
    op.execute(
        """
        INSERT INTO foreshadows (
            id, project_id, entry_id, planted_chapter_id, expected_chapter_id,
            description, resolved, resolved_chapter_id, created_at, updated_at
        )
        SELECT
            'fs_' || substring(md5(entry.id), 1, 28),
            entry.project_id,
            entry.id,
            entry.planted_at,
            entry.expected_by,
            entry.description,
            false,
            NULL,
            now(),
            now()
        FROM codex_entries entry
        JOIN chapters planted
          ON planted.id = entry.planted_at
         AND planted.project_id = entry.project_id
        LEFT JOIN chapters expected
          ON expected.id = entry.expected_by
         AND expected.project_id = entry.project_id
        WHERE entry.kind = 'event'
          AND entry.planted_at IS NOT NULL
          AND (entry.expected_by IS NULL OR expected.id IS NOT NULL)
        ON CONFLICT (entry_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("uq_foreshadow_entry", table_name="foreshadows")
