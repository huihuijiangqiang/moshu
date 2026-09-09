"""Add durable worker leases to long-generation segments."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "040_long_generation_leases"
down_revision: str | None = "039_long_generation_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("generation_segments", sa.Column("lease_owner", sa.String(100)))
    op.add_column(
        "generation_segments",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "generation_segments",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_generation_segments_lease_expires_at",
        "generation_segments",
        ["lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_generation_segments_lease_expires_at",
        table_name="generation_segments",
    )
    op.drop_column("generation_segments", "heartbeat_at")
    op.drop_column("generation_segments", "lease_expires_at")
    op.drop_column("generation_segments", "lease_owner")
