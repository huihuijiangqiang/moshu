"""move codex embeddings to 2048-dimensional halfvec

Revision ID: 002_embedding_halfvec_2048
Revises: 001_initial
Create Date: 2026-08-31 18:00:00.000000

Existing 1536-dimensional vectors cannot be converted into meaningful 2048-dimensional
vectors. The migration deliberately clears vectors and their source hashes so the normal
backfill task regenerates them with the configured model.
"""

from typing import Sequence, Union

from pgvector.sqlalchemy import HALFVEC, Vector

from alembic import op

revision: str = "002_embedding_halfvec_2048"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_codex_entries_embedding", table_name="codex_entries")
    op.execute(
        "UPDATE codex_entries SET embedding = NULL, embedding_text_hash = NULL "
        "WHERE embedding IS NOT NULL OR embedding_text_hash IS NOT NULL"
    )
    op.alter_column(
        "codex_entries",
        "embedding",
        existing_type=Vector(1536),
        type_=HALFVEC(2048),
        existing_nullable=True,
        postgresql_using="embedding::halfvec(2048)",
    )
    op.create_index(
        "ix_codex_entries_embedding",
        "codex_entries",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "halfvec_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_codex_entries_embedding", table_name="codex_entries")
    op.execute(
        "UPDATE codex_entries SET embedding = NULL, embedding_text_hash = NULL "
        "WHERE embedding IS NOT NULL OR embedding_text_hash IS NOT NULL"
    )
    op.alter_column(
        "codex_entries",
        "embedding",
        existing_type=HALFVEC(2048),
        type_=Vector(1536),
        existing_nullable=True,
        postgresql_using="embedding::vector(1536)",
    )
    op.create_index(
        "ix_codex_entries_embedding",
        "codex_entries",
        ["embedding"],
        postgresql_using="hnsw",
    )
