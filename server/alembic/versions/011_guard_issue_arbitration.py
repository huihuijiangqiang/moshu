"""Add persistent LLM arbitration state to guard issues.

Revision ID: 011_guard_issue_arbitration
Revises: 010_claim_temporal_evidence
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "011_guard_issue_arbitration"
down_revision: str | None = "010_claim_temporal_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "guard_issues",
        sa.Column(
            "arbitration_status",
            sa.String(20),
            server_default="not_requested",
            nullable=False,
        ),
    )
    op.add_column("guard_issues", sa.Column("arbitration_confidence", sa.Numeric(4, 3)))
    op.add_column("guard_issues", sa.Column("arbitration_rationale", sa.Text()))
    op.add_column("guard_issues", sa.Column("arbitration_model", sa.String(100)))
    op.add_column("guard_issues", sa.Column("arbitration_version", sa.String(50)))
    op.add_column("guard_issues", sa.Column("arbitration_error", sa.Text()))
    op.add_column("guard_issues", sa.Column("arbitrated_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "ck_guard_issue_arbitration_status",
        "guard_issues",
        "arbitration_status IN ('not_requested', 'pending', 'supported', 'unsupported', 'uncertain', 'failed')",
    )
    op.create_check_constraint(
        "ck_guard_issue_arbitration_confidence",
        "guard_issues",
        "arbitration_confidence IS NULL OR (arbitration_confidence >= 0.0 AND arbitration_confidence <= 1.0)",
    )
    op.create_index(
        "ix_guard_issues_arbitration_status",
        "guard_issues",
        ["arbitration_status"],
    )
    op.create_index(
        "ix_guard_issue_run_arbitration",
        "guard_issues",
        ["run_id", "arbitration_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_guard_issue_run_arbitration", table_name="guard_issues")
    op.drop_index("ix_guard_issues_arbitration_status", table_name="guard_issues")
    op.drop_constraint("ck_guard_issue_arbitration_confidence", "guard_issues", type_="check")
    op.drop_constraint("ck_guard_issue_arbitration_status", "guard_issues", type_="check")
    for column in (
        "arbitrated_at",
        "arbitration_error",
        "arbitration_version",
        "arbitration_model",
        "arbitration_rationale",
        "arbitration_confidence",
        "arbitration_status",
    ):
        op.drop_column("guard_issues", column)
