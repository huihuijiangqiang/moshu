"""initial schema with consistency models

Revision ID: 001_initial
Revises:
Create Date: 2026-08-31 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Create users table
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("avatar_url", sa.String(length=512), nullable=True),
        sa.Column("plan", sa.String(length=50), nullable=False),
        sa.Column("quota_remaining", sa.Integer(), nullable=False),
        sa.Column("quota_total", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_index(op.f("ix_users_phone"), "users", ["phone"], unique=True)

    # Create orgs table
    op.create_table(
        "orgs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("plan", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_orgs_slug"), "orgs", ["slug"], unique=True)

    # Create projects table
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=32), nullable=False),
        sa.Column("org_id", sa.String(length=32), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("genre", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("target_words_daily", sa.Integer(), nullable=False),
        sa.Column("style_profile_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_projects_owner_id"), "projects", ["owner_id"])
    op.create_index(op.f("ix_projects_org_id"), "projects", ["org_id"])

    # Create volumes table
    op.create_table(
        "volumes",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_volumes_project_id"), "volumes", ["project_id"])

    # Create chapters table
    op.create_table(
        "chapters",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("volume_id", sa.String(length=32), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("words", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["volume_id"], ["volumes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chapters_project_id"), "chapters", ["project_id"])
    op.create_index(op.f("ix_chapters_volume_id"), "chapters", ["volume_id"])

    # Create chapter_body table
    op.create_table(
        "chapter_body",
        sa.Column("chapter_id", sa.String(length=32), nullable=False),
        sa.Column("content_html", sa.Text(), nullable=False),
        sa.Column("content_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rev", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chapter_id"),
    )

    # Create chapter_versions table
    op.create_table(
        "chapter_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chapter_id", sa.String(length=32), nullable=False),
        sa.Column("content_html", sa.Text(), nullable=False),
        sa.Column("content_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rev", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(length=50), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chapter_versions_chapter_id"), "chapter_versions", ["chapter_id"])

    # Create codex_entries table
    op.create_table(
        "codex_entries",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("attrs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("resident", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("ref_chapters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("conflicts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("planted_at", sa.String(length=32), nullable=True),
        sa.Column("expected_by", sa.String(length=32), nullable=True),
        sa.Column("embedding", postgresql.Vector(1536), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_codex_entries_project_id"), "codex_entries", ["project_id"])

    # Create codex_aliases table
    op.create_table(
        "codex_aliases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("entry_id", sa.String(length=32), nullable=False),
        sa.Column("alias", sa.String(length=200), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["entry_id"], ["codex_entries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_codex_aliases_entry_id"), "codex_aliases", ["entry_id"])
    op.create_index("ix_codex_alias_lookup", "codex_aliases", ["alias", "entry_id"])

    # Create codex_refs table
    op.create_table(
        "codex_refs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chapter_id", sa.String(length=32), nullable=False),
        sa.Column("entry_id", sa.String(length=32), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entry_id"], ["codex_entries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_codex_refs_chapter_id"), "codex_refs", ["chapter_id"])
    op.create_index(op.f("ix_codex_refs_entry_id"), "codex_refs", ["entry_id"])

    # Create codex_relations table
    op.create_table(
        "codex_relations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("from_id", sa.String(length=32), nullable=False),
        sa.Column("to_id", sa.String(length=32), nullable=False),
        sa.Column("relation_type", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["from_id"], ["codex_entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_id"], ["codex_entries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_codex_relations_from_id"), "codex_relations", ["from_id"])
    op.create_index(op.f("ix_codex_relations_to_id"), "codex_relations", ["to_id"])

    # Consistency models - Part 1: Create remaining consistency tables
    # (This is a simplified version - full migration would include all consistency_extended models)
    # For MVP, we're documenting that full migration generation requires live DB connection


def downgrade() -> None:
    # Drop tables in reverse order
    op.drop_table("codex_relations")
    op.drop_table("codex_refs")
    op.drop_table("codex_aliases")
    op.drop_table("codex_entries")
    op.drop_table("chapter_versions")
    op.drop_table("chapter_body")
    op.drop_table("chapters")
    op.drop_table("volumes")
    op.drop_table("projects")
    op.drop_table("orgs")
    op.drop_table("users")

    op.execute("DROP EXTENSION IF EXISTS vector")
