"""Add versioned semantic chunks for chapter body retrieval."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy.dialects import postgresql


revision: str = "034_chapter_chunks"
down_revision: str | None = "033_payment_provider_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chapter_chunks",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project_id", sa.String(32), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_id", sa.String(32), sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("body_rev", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("paragraph_start", sa.Integer(), nullable=False),
        sa.Column("paragraph_end", sa.Integer(), nullable=False),
        sa.Column("paragraph_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("embedding", HALFVEC(2048), nullable=True),
        sa.Column("embedding_text_hash", sa.String(64), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("chapter_id", "body_rev", "chunk_index", name="uq_chapter_chunk_revision_index"),
        sa.CheckConstraint("body_rev >= 0", name="ck_chapter_chunk_body_rev_nonnegative"),
        sa.CheckConstraint("chunk_index >= 0", name="ck_chapter_chunk_index_nonnegative"),
        sa.CheckConstraint("paragraph_start >= 0 AND paragraph_end >= paragraph_start", name="ck_chapter_chunk_paragraph_range"),
        sa.CheckConstraint("status IN ('pending', 'ready', 'stale', 'failed')", name="ck_chapter_chunk_status"),
    )
    op.create_index("ix_chapter_chunks_project_id", "chapter_chunks", ["project_id"])
    op.create_index("ix_chapter_chunks_chapter_id", "chapter_chunks", ["chapter_id"])
    op.create_index(
        "ix_chapter_chunks_current_lookup",
        "chapter_chunks",
        ["project_id", "chapter_id", "body_rev", "status"],
    )
    op.create_index(
        "ix_chapter_chunks_embedding",
        "chapter_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "halfvec_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_chapter_chunks_embedding", table_name="chapter_chunks")
    op.drop_index("ix_chapter_chunks_current_lookup", table_name="chapter_chunks")
    op.drop_index("ix_chapter_chunks_chapter_id", table_name="chapter_chunks")
    op.drop_index("ix_chapter_chunks_project_id", table_name="chapter_chunks")
    op.drop_table("chapter_chunks")
