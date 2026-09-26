"""Keep embeddings from different models out of the same retrieval space."""

import sqlalchemy as sa
from alembic import op

revision = "046_embedding_space"
down_revision = "045_character_full_body"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Unknown legacy vectors are retained for recovery but excluded from retrieval
    # until an explicit backfill supplies their model identity. Prose is unchanged.
    for table in ("codex_entries", "chapter_chunks"):
        op.add_column(table, sa.Column("embedding_space_id", sa.String(64), nullable=True))


def downgrade() -> None:
    for table in ("chapter_chunks", "codex_entries"):
        op.drop_column(table, "embedding_space_id")
