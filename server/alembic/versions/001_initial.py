"""initial schema with all 30 tables

Revision ID: 001_initial
Revises:
Create Date: 2025-01-20 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
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

    # Core tables
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

    op.create_table(
        "orgs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("plan", sa.String(length=50), nullable=False),
        sa.Column("seats", sa.Integer(), nullable=False),
        sa.Column("seats_used", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "style_profiles",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("sample_words", sa.Integer(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("dimensions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("alignment", sa.Float(), nullable=False),
        sa.Column("upload_url", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_style_profiles_user_id"), "style_profiles", ["user_id"])

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

    op.create_table(
        "org_members",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "user_id", name="uq_org_user"),
    )
    op.create_index(op.f("ix_org_members_org_id"), "org_members", ["org_id"])
    op.create_index(op.f("ix_org_members_user_id"), "org_members", ["user_id"])

    op.create_table(
        "volumes",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_volumes_project_id"), "volumes", ["project_id"])

    op.create_table(
        "chapters",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("volume_id", sa.String(length=32), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("words", sa.Integer(), nullable=False),
        sa.Column("outline", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["volume_id"], ["volumes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chapters_project_id"), "chapters", ["project_id"])

    op.create_table(
        "chapter_bodies",
        sa.Column("chapter_id", sa.String(length=32), nullable=False),
        sa.Column("content_html", sa.Text(), nullable=False),
        sa.Column("content_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rev", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chapter_id"),
    )

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

    op.create_table(
        "chapter_assignments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chapter_id", sa.String(length=32), nullable=False),
        sa.Column("assigned_to", sa.String(length=32), nullable=False),
        sa.Column("assigned_by", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assigned_to"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chapter_assignments_chapter_id"), "chapter_assignments", ["chapter_id"])
    op.create_index(op.f("ix_chapter_assignments_assigned_to"), "chapter_assignments", ["assigned_to"])

    # Codex tables
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
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("embedding_text_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('confirmed', 'pending')", name="ck_codex_entry_status"),
    )
    op.create_index(op.f("ix_codex_entries_project_id"), "codex_entries", ["project_id"])
    op.create_index("ix_codex_entries_embedding", "codex_entries", ["embedding"], postgresql_using="hnsw")

    op.create_table(
        "codex_aliases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("entry_id", sa.String(length=32), nullable=False),
        sa.Column("alias", sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(["entry_id"], ["codex_entries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_codex_aliases_entry_id"), "codex_aliases", ["entry_id"])
    op.create_index(op.f("ix_codex_aliases_alias"), "codex_aliases", ["alias"])
    op.create_index("ix_codex_aliases_alias_gin", "codex_aliases", ["alias"], postgresql_using="gin")

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
    op.create_index("ix_codex_refs_chapter_entry", "codex_refs", ["chapter_id", "entry_id"], unique=True)

    op.create_table(
        "codex_relations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("from_id", sa.String(length=32), nullable=False),
        sa.Column("to_id", sa.String(length=32), nullable=False),
        sa.Column("relation_type", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["from_id"], ["codex_entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_id"], ["codex_entries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_codex_relations_from_id"), "codex_relations", ["from_id"])
    op.create_index(op.f("ix_codex_relations_to_id"), "codex_relations", ["to_id"])

    # Consistency tables
    op.create_table(
        "chapter_outline_states",
        sa.Column("chapter_id", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.Column("body_needs_revision", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("marked_outline_rev", sa.Integer(), nullable=True),
        sa.Column("marked_body_rev", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("revision >= 0", name="ck_outline_state_revision_nonnegative"),
        sa.CheckConstraint(
            "(body_needs_revision AND marked_outline_rev IS NOT NULL AND marked_body_rev IS NOT NULL) "
            "OR (NOT body_needs_revision AND marked_outline_rev IS NULL AND marked_body_rev IS NULL)",
            name="ck_outline_state_marker_consistent",
        ),
        sa.CheckConstraint(
            "marked_outline_rev IS NULL OR marked_outline_rev <= revision",
            name="ck_outline_state_marked_revision_valid",
        ),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chapter_id"),
    )

    op.create_table(
        "chapter_outline_revisions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chapter_id", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("nodes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("body_policy", sa.String(length=32), nullable=False),
        sa.Column("body_rev_at_change", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "body_policy IN ('plan_only', 'mark_body_for_revision')",
            name="ck_body_policy_enum",
        ),
        sa.CheckConstraint("revision > 0", name="ck_outline_revision_positive"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chapter_id", "revision", name="uq_chapter_outline_revision"),
    )
    op.create_index(op.f("ix_chapter_outline_revisions_chapter_id"), "chapter_outline_revisions", ["chapter_id"])

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("topic", sa.String(length=100), nullable=False),
        sa.Column("aggregate_id", sa.String(length=64), nullable=False),
        sa.Column("aggregate_rev", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lease_owner", sa.String(length=100), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'dispatching', 'sent', 'failed', 'dead_letter')",
            name="ck_outbox_status_enum",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_outbox_attempts_nonnegative"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("topic", "aggregate_id", "aggregate_rev", name="uq_outbox_event_key"),
    )
    op.create_index(op.f("ix_outbox_events_topic"), "outbox_events", ["topic"])
    op.create_index(op.f("ix_outbox_events_status"), "outbox_events", ["status"])

    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("scope", sa.String(length=100), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("owner_token", sa.String(length=64), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'completed')", name="ck_idempotency_status_enum"),
        sa.CheckConstraint(
            "(status = 'pending' AND response_status IS NULL AND response_body IS NULL) OR "
            "(status = 'completed' AND owner_token IS NULL AND lease_until IS NULL "
            "AND response_status IS NOT NULL AND response_body IS NOT NULL)",
            name="ck_idempotency_phase_consistent",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope", "key", name="uq_idempotency_scope_key"),
    )

    # Consistency extended tables
    op.create_table(
        "consistency_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("chapter_id", sa.String(length=32), nullable=False),
        sa.Column("body_rev", sa.Integer(), nullable=False),
        sa.Column("pipeline_version", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("trigger", sa.String(length=50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'extracting', 'summarizing', 'scanning', 'completed', 'failed')", name="ck_consistency_run_status"),
        sa.CheckConstraint("trigger IN ('body_save', 'manual_scan', 'pipeline_upgrade', 'maintenance')", name="ck_consistency_run_trigger"),
        sa.CheckConstraint("body_rev > 0", name="ck_consistency_run_body_rev_positive"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chapter_id", "body_rev", "pipeline_version", name="uq_consistency_run_key"),
    )
    op.create_index(op.f("ix_consistency_runs_project_id"), "consistency_runs", ["project_id"])
    op.create_index(op.f("ix_consistency_runs_chapter_id"), "consistency_runs", ["chapter_id"])
    op.create_index(op.f("ix_consistency_runs_status"), "consistency_runs", ["status"])

    op.create_table(
        "document_summaries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("owner_type", sa.String(length=20), nullable=False),
        sa.Column("owner_id", sa.String(length=32), nullable=False),
        sa.Column("source_rev", sa.Integer(), nullable=False),
        sa.Column("summary_version", sa.String(length=50), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("covered_chapter_from", sa.Integer(), nullable=True),
        sa.Column("covered_chapter_to", sa.Integer(), nullable=True),
        sa.Column("model_id", sa.String(length=100), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("owner_type IN ('chapter', 'volume')", name="ck_summary_owner_type"),
        sa.CheckConstraint("status IN ('active', 'stale', 'superseded')", name="ck_summary_status"),
        sa.CheckConstraint("source_rev > 0", name="ck_summary_source_rev_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_type", "owner_id", "source_rev", "summary_version", name="uq_document_summary_key"),
    )
    op.create_index(op.f("ix_document_summaries_owner_type"), "document_summaries", ["owner_type"])
    op.create_index(op.f("ix_document_summaries_owner_id"), "document_summaries", ["owner_id"])
    op.create_index("ix_document_summary_owner", "document_summaries", ["owner_type", "owner_id", "status"])

    op.create_table(
        "consistency_claims",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("subject_entry_id", sa.String(length=32), nullable=True),
        sa.Column("subject_text", sa.String(length=200), nullable=False),
        sa.Column("predicate", sa.String(length=100), nullable=False),
        sa.Column("object_type", sa.String(length=50), nullable=False),
        sa.Column("object_value", sa.Text(), nullable=True),
        sa.Column("object_entry_id", sa.String(length=32), nullable=True),
        sa.Column("polarity", sa.String(length=20), nullable=False),
        sa.Column("certainty", sa.String(length=20), nullable=False),
        sa.Column("source_kind", sa.String(length=20), nullable=False),
        sa.Column("chapter_id", sa.String(length=32), nullable=True),
        sa.Column("body_rev", sa.Integer(), nullable=True),
        sa.Column("outline_rev", sa.Integer(), nullable=True),
        sa.Column("paragraph_id", sa.String(length=100), nullable=True),
        sa.Column("source_anchor", sa.String(length=64), nullable=True),
        sa.Column("timeline_id", sa.String(length=32), nullable=True),
        sa.Column("story_order", sa.Numeric(24, 8), nullable=True),
        sa.Column("valid_from_order", sa.Numeric(24, 8), nullable=True),
        sa.Column("valid_to_order", sa.Numeric(24, 8), nullable=True),
        sa.Column("extractor_version", sa.String(length=50), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("polarity IN ('positive', 'negative')", name="ck_claim_polarity"),
        sa.CheckConstraint("certainty IN ('explicit', 'inferred', 'uncertain')", name="ck_claim_certainty"),
        sa.CheckConstraint("source_kind IN ('codex', 'body', 'outline', 'resolution')", name="ck_claim_source_kind"),
        sa.CheckConstraint("status IN ('candidate', 'accepted', 'rejected', 'superseded')", name="ck_claim_status"),
        sa.CheckConstraint("object_type IN ('scalar', 'entity', 'location', 'ability', 'timestamp')", name="ck_claim_object_type"),
        sa.CheckConstraint(
            "(source_kind = 'body' AND chapter_id IS NOT NULL AND body_rev IS NOT NULL) OR "
            "(source_kind = 'outline' AND chapter_id IS NOT NULL AND outline_rev IS NOT NULL) OR "
            "(source_kind IN ('codex', 'resolution'))",
            name="ck_claim_source_versioning"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subject_entry_id"], ["codex_entries.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["object_entry_id"], ["codex_entries.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_consistency_claims_project_id"), "consistency_claims", ["project_id"])
    op.create_index(op.f("ix_consistency_claims_subject_entry_id"), "consistency_claims", ["subject_entry_id"])
    op.create_index(op.f("ix_consistency_claims_predicate"), "consistency_claims", ["predicate"])
    op.create_index(op.f("ix_consistency_claims_source_kind"), "consistency_claims", ["source_kind"])
    op.create_index(op.f("ix_consistency_claims_chapter_id"), "consistency_claims", ["chapter_id"])
    op.create_index(op.f("ix_consistency_claims_timeline_id"), "consistency_claims", ["timeline_id"])
    op.create_index(op.f("ix_consistency_claims_fingerprint"), "consistency_claims", ["fingerprint"])
    op.create_index(op.f("ix_consistency_claims_status"), "consistency_claims", ["status"])
    op.create_index("ix_claim_subject_predicate", "consistency_claims", ["subject_entry_id", "predicate", "status"])
    op.create_index("ix_claim_timeline_order", "consistency_claims", ["timeline_id", "story_order"])
    # Partial unique indexes for source-specific deduplication
    op.create_index(
        "uq_claim_body_source",
        "consistency_claims",
        ["chapter_id", "body_rev", "fingerprint", "extractor_version"],
        unique=True,
        postgresql_where=sa.text("source_kind = 'body'")
    )
    op.create_index(
        "uq_claim_outline_source",
        "consistency_claims",
        ["chapter_id", "outline_rev", "fingerprint", "extractor_version"],
        unique=True,
        postgresql_where=sa.text("source_kind = 'outline'")
    )
    op.create_index(
        "uq_claim_codex_source",
        "consistency_claims",
        ["fingerprint", "extractor_version"],
        unique=True,
        postgresql_where=sa.text("source_kind = 'codex'")
    )
    op.create_index(
        "uq_claim_resolution_source",
        "consistency_claims",
        ["fingerprint", "extractor_version"],
        unique=True,
        postgresql_where=sa.text("source_kind = 'resolution'")
    )

    op.create_table(
        "story_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("timeline_id", sa.String(length=32), nullable=False),
        sa.Column("story_order", sa.Numeric(24, 8), nullable=True),
        sa.Column("time_text", sa.String(length=200), nullable=True),
        sa.Column("time_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("time_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("chapter_id", sa.String(length=32), nullable=False),
        sa.Column("body_rev", sa.Integer(), nullable=False),
        sa.Column("paragraph_id", sa.String(length=100), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("extractor_version", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('candidate', 'confirmed', 'rejected', 'superseded')", name="ck_event_status"),
        sa.CheckConstraint("body_rev > 0", name="ck_event_body_rev_positive"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_story_events_project_id"), "story_events", ["project_id"])
    op.create_index(op.f("ix_story_events_timeline_id"), "story_events", ["timeline_id"])
    op.create_index(op.f("ix_story_events_chapter_id"), "story_events", ["chapter_id"])
    op.create_index(op.f("ix_story_events_status"), "story_events", ["status"])
    op.create_index("ix_event_timeline_order", "story_events", ["timeline_id", "story_order"])
    op.create_index("ix_event_chapter", "story_events", ["chapter_id", "body_rev"])

    op.create_table(
        "entity_state_intervals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("entry_id", sa.String(length=32), nullable=False),
        sa.Column("state_key", sa.String(length=100), nullable=False),
        sa.Column("value_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("valid_from_order", sa.Numeric(24, 8), nullable=False),
        sa.Column("valid_to_order", sa.Numeric(24, 8), nullable=True),
        sa.Column("timeline_id", sa.String(length=32), nullable=False),
        sa.Column("source_claim_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('candidate', 'accepted', 'rejected', 'superseded')", name="ck_state_interval_status"),
        sa.CheckConstraint("valid_to_order IS NULL OR valid_to_order > valid_from_order", name="ck_state_interval_order_valid"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entry_id"], ["codex_entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_claim_id"], ["consistency_claims.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_entity_state_intervals_project_id"), "entity_state_intervals", ["project_id"])
    op.create_index(op.f("ix_entity_state_intervals_entry_id"), "entity_state_intervals", ["entry_id"])
    op.create_index(op.f("ix_entity_state_intervals_state_key"), "entity_state_intervals", ["state_key"])
    op.create_index(op.f("ix_entity_state_intervals_timeline_id"), "entity_state_intervals", ["timeline_id"])
    op.create_index(op.f("ix_entity_state_intervals_status"), "entity_state_intervals", ["status"])
    op.create_index("ix_state_interval_timeline", "entity_state_intervals", ["timeline_id", "valid_from_order", "valid_to_order"])
    op.create_index("ix_state_interval_entry_key", "entity_state_intervals", ["entry_id", "state_key", "status"])

    # Guard tables
    op.create_table(
        "guard_issues",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("chapter_id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("entry_id", sa.String(length=32), nullable=True),
        sa.Column("issue_type", sa.String(length=50), nullable=False),
        sa.Column("rule_version", sa.String(length=50), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("anchor", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("actions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("issue_rev", sa.Integer(), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("resolution", sa.String(length=50), nullable=True),
        sa.Column("false_positive", sa.Boolean(), nullable=False),
        sa.Column("stale_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('open', 'resolved', 'stale', 'false_positive')", name="ck_guard_issue_status"),
        sa.CheckConstraint("severity IN ('low', 'medium', 'high')", name="ck_guard_issue_severity"),
        sa.CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="ck_guard_issue_confidence"),
        sa.CheckConstraint("issue_rev > 0", name="ck_guard_issue_rev_positive"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["consistency_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entry_id"], ["codex_entries.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "fingerprint", name="uq_guard_issue_fingerprint"),
    )
    op.create_index(op.f("ix_guard_issues_project_id"), "guard_issues", ["project_id"])
    op.create_index(op.f("ix_guard_issues_chapter_id"), "guard_issues", ["chapter_id"])
    op.create_index(op.f("ix_guard_issues_run_id"), "guard_issues", ["run_id"])
    op.create_index(op.f("ix_guard_issues_issue_type"), "guard_issues", ["issue_type"])
    op.create_index(op.f("ix_guard_issues_fingerprint"), "guard_issues", ["fingerprint"])
    op.create_index(op.f("ix_guard_issues_status"), "guard_issues", ["status"])
    op.create_index("ix_guard_issue_status_stale", "guard_issues", ["status", "stale_at"])

    op.create_table(
        "guard_issue_evidence",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("issue_id", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=20), nullable=False),
        sa.Column("source_kind", sa.String(length=20), nullable=False),
        sa.Column("chapter_id", sa.String(length=32), nullable=True),
        sa.Column("body_rev", sa.Integer(), nullable=True),
        sa.Column("outline_rev", sa.Integer(), nullable=True),
        sa.Column("codex_entry_id", sa.String(length=32), nullable=True),
        sa.Column("paragraph_id", sa.String(length=100), nullable=True),
        sa.Column("start_offset", sa.Integer(), nullable=True),
        sa.Column("end_offset", sa.Integer(), nullable=True),
        sa.Column("offset_encoding", sa.String(length=20), server_default="utf16", nullable=False),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("quote_hash", sa.String(length=64), nullable=True),
        sa.Column("anchor_version", sa.String(length=20), server_default="v1", nullable=False),
        sa.Column("claim_id", sa.BigInteger(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("side IN ('expected', 'actual', 'context')", name="ck_evidence_side"),
        sa.CheckConstraint("source_kind IN ('codex', 'body', 'outline', 'claim')", name="ck_evidence_source_kind"),
        sa.CheckConstraint("offset_encoding IN ('utf8', 'utf16', 'codepoint')", name="ck_evidence_offset_encoding"),
        sa.CheckConstraint(
            "(source_kind = 'body' AND chapter_id IS NOT NULL AND body_rev IS NOT NULL) OR "
            "(source_kind = 'outline' AND chapter_id IS NOT NULL AND outline_rev IS NOT NULL) OR "
            "(source_kind = 'codex' AND codex_entry_id IS NOT NULL) OR "
            "(source_kind = 'claim' AND claim_id IS NOT NULL)",
            name="ck_evidence_source_required"
        ),
        sa.ForeignKeyConstraint(["issue_id"], ["guard_issues.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["claim_id"], ["consistency_claims.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["codex_entry_id"], ["codex_entries.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_guard_issue_evidence_issue_id"), "guard_issue_evidence", ["issue_id"])
    op.create_index("ix_evidence_issue_side", "guard_issue_evidence", ["issue_id", "side", "sort_order"])

    op.create_table(
        "guard_resolutions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("issue_id", sa.String(length=32), nullable=False),
        sa.Column("issue_rev", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "action IN ('accept_old_fact', 'accept_new_fact', 'intentional_exception', "
            "'false_positive', 'fixed_in_body', 'defer')",
            name="ck_resolution_action",
        ),
        sa.CheckConstraint("issue_rev > 0", name="ck_resolution_issue_rev_positive"),
        sa.ForeignKeyConstraint(["issue_id"], ["guard_issues.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_guard_resolutions_issue_id"), "guard_resolutions", ["issue_id"])
    op.create_index("ix_resolution_issue", "guard_resolutions", ["issue_id", "created_at"])

    op.create_table(
        "foreshadows",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("entry_id", sa.String(length=32), nullable=False),
        sa.Column("planted_chapter_id", sa.String(length=32), nullable=False),
        sa.Column("expected_chapter_id", sa.String(length=32), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("resolved_chapter_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entry_id"], ["codex_entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["planted_chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["expected_chapter_id"], ["chapters.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_foreshadows_project_id"), "foreshadows", ["project_id"])
    op.create_index(op.f("ix_foreshadows_entry_id"), "foreshadows", ["entry_id"])

    # Usage tables
    op.create_table(
        "generation_runs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("chapter_id", sa.String(length=32), nullable=False),
        sa.Column("task_type", sa.String(length=50), nullable=False),
        sa.Column("model_tier", sa.String(length=20), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("generated_words", sa.Integer(), nullable=False),
        sa.Column("accepted_words", sa.Integer(), nullable=False),
        sa.Column("layer_report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_generation_runs_user_id"), "generation_runs", ["user_id"])
    op.create_index(op.f("ix_generation_runs_project_id"), "generation_runs", ["project_id"])
    op.create_index(op.f("ix_generation_runs_chapter_id"), "generation_runs", ["chapter_id"])

    op.create_table(
        "usage_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("feature", sa.String(length=50), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_usage_logs_user_id"), "usage_logs", ["user_id"])
    op.create_index(op.f("ix_usage_logs_timestamp"), "usage_logs", ["timestamp"])

    op.create_table(
        "ratio_reports",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("total_words", sa.Integer(), nullable=False),
        sa.Column("ai_raw_words", sa.Integer(), nullable=False),
        sa.Column("ai_edited_words", sa.Integer(), nullable=False),
        sa.Column("human_words", sa.Integer(), nullable=False),
        sa.Column("suspect_count", sa.Integer(), nullable=False),
        sa.Column("paragraphs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("suspects", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ratio_reports_project_id"), "ratio_reports", ["project_id"])


def downgrade() -> None:
    # Drop tables in reverse order
    op.drop_table("ratio_reports")
    op.drop_table("usage_logs")
    op.drop_table("generation_runs")
    op.drop_table("foreshadows")
    op.drop_table("guard_resolutions")
    op.drop_table("guard_issue_evidence")
    op.drop_table("guard_issues")
    op.drop_table("entity_state_intervals")
    op.drop_table("story_events")
    op.drop_table("consistency_claims")
    op.drop_table("document_summaries")
    op.drop_table("consistency_runs")
    op.drop_table("idempotency_records")
    op.drop_table("outbox_events")
    op.drop_table("chapter_outline_revisions")
    op.drop_table("chapter_outline_states")
    op.drop_table("codex_relations")
    op.drop_table("codex_refs")
    op.drop_table("codex_aliases")
    op.drop_table("codex_entries")
    op.drop_table("chapter_assignments")
    op.drop_table("chapter_versions")
    op.drop_table("chapter_bodies")
    op.drop_table("chapters")
    op.drop_table("volumes")
    op.drop_table("org_members")
    op.drop_table("projects")
    op.drop_table("style_profiles")
    op.drop_table("orgs")
    op.drop_table("users")

    op.execute("DROP EXTENSION IF EXISTS vector")
