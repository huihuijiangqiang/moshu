"""Add paragraph-level naturalization review persistence."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "031_naturalization_review"
down_revision: str | None = "030_chapter_scenes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb_default(value: str = "{}") -> sa.TextClause:
    return sa.text(f"'{value}'::jsonb")


def upgrade() -> None:
    jsonb = postgresql.JSONB()
    op.create_table(
        "naturalization_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(32), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_id", sa.String(32), sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_body_rev", sa.Integer(), nullable=False),
        sa.Column("source_content_hash", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False, server_default="chapter"),
        sa.Column("mode", sa.String(20), nullable=False, server_default="rules"),
        sa.Column("status", sa.String(20), nullable=False, server_default="ready"),
        sa.Column("style_profile_id", sa.String(32), nullable=True),
        sa.Column("prompt_version", sa.String(40), nullable=False, server_default="naturalize-rules-v1"),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("finding_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accepted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("scope IN ('chapter', 'selection')", name="ck_naturalization_run_scope"),
        sa.CheckConstraint("mode IN ('rules', 'assisted')", name="ck_naturalization_run_mode"),
        sa.CheckConstraint(
            "status IN ('scanning', 'ready', 'stale', 'failed', 'completed')",
            name="ck_naturalization_run_status",
        ),
        sa.CheckConstraint("source_body_rev >= 0", name="ck_naturalization_run_body_rev_nonnegative"),
    )
    op.create_index("ix_naturalization_runs_user_id", "naturalization_runs", ["user_id"])
    op.create_index("ix_naturalization_runs_project_id", "naturalization_runs", ["project_id"])
    op.create_index("ix_naturalization_runs_chapter_id", "naturalization_runs", ["chapter_id"])
    op.create_index("ix_naturalization_runs_status", "naturalization_runs", ["status"])
    op.create_index("ix_naturalization_runs_chapter_created", "naturalization_runs", ["chapter_id", "created_at"])

    op.create_table(
        "naturalization_findings",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("naturalization_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("paragraph_id", sa.String(120), nullable=False),
        sa.Column("start", sa.Integer(), nullable=False),
        sa.Column("end", sa.Integer(), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("candidate_text", sa.Text(), nullable=False),
        sa.Column("source_text_hash", sa.String(64), nullable=False),
        sa.Column("rule_ids", jsonb, nullable=False, server_default=_jsonb_default("[]")),
        sa.Column("reasons", jsonb, nullable=False, server_default=_jsonb_default("[]")),
        sa.Column("locked_facts", jsonb, nullable=False, server_default=_jsonb_default()),
        sa.Column("validation", jsonb, nullable=False, server_default=_jsonb_default()),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint('start >= 0 AND "end" > start', name="ck_naturalization_finding_range"),
        sa.CheckConstraint("revision > 0", name="ck_naturalization_finding_revision_positive"),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected', 'stale')",
            name="ck_naturalization_finding_status",
        ),
    )
    op.create_index("ix_naturalization_findings_run_id", "naturalization_findings", ["run_id"])
    op.create_index("ix_naturalization_findings_paragraph_id", "naturalization_findings", ["paragraph_id"])
    op.create_index("ix_naturalization_findings_status", "naturalization_findings", ["status"])
    op.create_index("ix_naturalization_findings_run_status", "naturalization_findings", ["run_id", "status"])


def downgrade() -> None:
    op.drop_table("naturalization_findings")
    op.drop_table("naturalization_runs")
