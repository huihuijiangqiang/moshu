"""Persist auditable claim temporal evidence for cross-chapter anchors.

Revision ID: 010_claim_temporal_evidence
Revises: 009_embedding_dispatch_attempts
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "010_claim_temporal_evidence"
down_revision: str | None = "009_embedding_dispatch_attempts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("consistency_claims", sa.Column("temporal_anchor_text", sa.String(200)))
    op.add_column("consistency_claims", sa.Column("temporal_anchor_value", sa.String(64)))
    op.add_column("consistency_claims", sa.Column("temporal_event_ref", sa.String(200)))
    op.add_column("consistency_claims", sa.Column("temporal_relation", sa.String(20)))
    op.add_column("consistency_claims", sa.Column("temporal_relation_ref", sa.String(200)))
    op.add_column("consistency_claims", sa.Column("order_basis", sa.String(32)))
    op.add_column("consistency_claims", sa.Column("order_confidence", sa.Numeric(5, 4)))
    op.create_check_constraint(
        "ck_claim_temporal_relation",
        "consistency_claims",
        "temporal_relation IS NULL OR temporal_relation IN ('before', 'after', 'simultaneous')",
    )
    op.create_check_constraint(
        "ck_claim_order_basis",
        "consistency_claims",
        "order_basis IS NULL OR order_basis IN "
        "('absolute_datetime', 'relative_to_anchor', 'narration_local', 'unknown')",
    )
    op.create_index(
        "ix_claim_temporal_event_ref",
        "consistency_claims",
        ["project_id", "timeline_id", "temporal_event_ref", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_claim_temporal_event_ref", table_name="consistency_claims")
    op.drop_constraint("ck_claim_order_basis", "consistency_claims", type_="check")
    op.drop_constraint("ck_claim_temporal_relation", "consistency_claims", type_="check")
    for column in (
        "order_confidence",
        "order_basis",
        "temporal_relation_ref",
        "temporal_relation",
        "temporal_event_ref",
        "temporal_anchor_value",
        "temporal_anchor_text",
    ):
        op.drop_column("consistency_claims", column)
