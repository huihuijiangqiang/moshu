"""Add platform positioning cards and immutable revisions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "029_platform_positioning"
down_revision: str | None = "028_adaptation_storyboard"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb_default() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def upgrade() -> None:
    jsonb = postgresql.JSONB()
    op.create_table(
        "project_positionings",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(32),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(20), nullable=False, server_default="general"),
        sa.Column("title_candidates", jsonb, nullable=False, server_default=_jsonb_default()),
        sa.Column("selling_point", sa.Text(), nullable=False, server_default=""),
        sa.Column("synopsis", sa.Text(), nullable=False, server_default=""),
        sa.Column("tags", jsonb, nullable=False, server_default=_jsonb_default()),
        sa.Column("protagonist_dilemma", sa.Text(), nullable=False, server_default=""),
        sa.Column("first_payoff", sa.Text(), nullable=False, server_default=""),
        sa.Column("long_term_arc", sa.Text(), nullable=False, server_default=""),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", name="uq_project_positioning_project"),
        sa.CheckConstraint(
            "platform IN ('fanqie', 'qimao', 'qidian', 'general')",
            name="ck_project_positioning_platform",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'archived')",
            name="ck_project_positioning_status",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_project_positioning_revision_nonnegative"),
    )
    op.create_index("ix_project_positionings_project_id", "project_positionings", ["project_id"])

    op.create_table(
        "project_positioning_revisions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "positioning_id",
            sa.String(32),
            sa.ForeignKey("project_positionings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.String(32),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("title_candidates", jsonb, nullable=False, server_default=_jsonb_default()),
        sa.Column("selling_point", sa.Text(), nullable=False, server_default=""),
        sa.Column("synopsis", sa.Text(), nullable=False, server_default=""),
        sa.Column("tags", jsonb, nullable=False, server_default=_jsonb_default()),
        sa.Column("protagonist_dilemma", sa.Text(), nullable=False, server_default=""),
        sa.Column("first_payoff", sa.Text(), nullable=False, server_default=""),
        sa.Column("long_term_arc", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("positioning_id", "revision", name="uq_project_positioning_revision"),
        sa.CheckConstraint("revision > 0", name="ck_project_positioning_revision_positive"),
        sa.CheckConstraint(
            "platform IN ('fanqie', 'qimao', 'qidian', 'general')",
            name="ck_project_positioning_revision_platform",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'archived')",
            name="ck_project_positioning_revision_status",
        ),
    )
    op.create_index(
        "ix_project_positioning_revisions_positioning_id",
        "project_positioning_revisions",
        ["positioning_id"],
    )
    op.create_index(
        "ix_project_positioning_revisions_project_id",
        "project_positioning_revisions",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_table("project_positioning_revisions")
    op.drop_table("project_positionings")
