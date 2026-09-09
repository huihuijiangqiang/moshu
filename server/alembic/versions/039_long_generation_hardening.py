"""Harden long-generation tables for installations already on revision 038.

Raw SQL is used intentionally here: the schema-parity harness models DDL
declared in 038, while this migration only repairs existing installations.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "039_long_generation_hardening"
down_revision: str | None = "038_long_generation_planning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("story_arcs", "plot_threads", "plot_beats", "generation_segments"):
        op.execute(f'ALTER TABLE "{table}" ALTER COLUMN created_at SET NOT NULL')
        op.execute(f'ALTER TABLE "{table}" ALTER COLUMN updated_at SET NOT NULL')
    indexes = {
        "story_arcs": ("project_id",),
        "plot_threads": ("project_id", "arc_id"),
        "plot_beats": ("project_id", "thread_id", "chapter_id"),
        "generation_segments": ("project_id", "chapter_id", "run_id"),
    }
    for table, columns in indexes.items():
        for column in columns:
            op.execute(f'CREATE INDEX IF NOT EXISTS "ix_{table}_{column}" ON "{table}" ("{column}")')


def downgrade() -> None:
    indexes = {
        "story_arcs": ("project_id",),
        "plot_threads": ("project_id", "arc_id"),
        "plot_beats": ("project_id", "thread_id", "chapter_id"),
        "generation_segments": ("project_id", "chapter_id", "run_id"),
    }
    for table, columns in indexes.items():
        for column in columns:
            op.execute(f'DROP INDEX IF EXISTS "ix_{table}_{column}"')
    for table in ("generation_segments", "plot_beats", "plot_threads", "story_arcs"):
        op.execute(f'ALTER TABLE "{table}" ALTER COLUMN created_at DROP NOT NULL')
        op.execute(f'ALTER TABLE "{table}" ALTER COLUMN updated_at DROP NOT NULL')
