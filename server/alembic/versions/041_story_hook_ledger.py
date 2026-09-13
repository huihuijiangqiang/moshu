"""Add a durable chapter-hook debt ledger."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "041_story_hook_ledger"
down_revision: str | None = "040_long_generation_leases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "story_hooks",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(32),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_chapter_id",
            sa.String(32),
            sa.ForeignKey("chapters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "payoff_chapter_id",
            sa.String(32),
            sa.ForeignKey("chapters.id", ondelete="SET NULL"),
        ),
        sa.Column("hook_type", sa.String(50), nullable=False),
        sa.Column("concrete_event", sa.Text(), nullable=False),
        sa.Column("unresolved_question", sa.Text(), nullable=False),
        sa.Column("payoff_by_chapter", sa.Integer()),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("novelty_signature", sa.String(64), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=False, server_default=""),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("project_id", "novelty_signature", name="uq_story_hook_signature"),
        sa.CheckConstraint(
            "status IN ('open', 'deferred', 'resolved', 'abandoned')",
            name="ck_story_hook_status",
        ),
        sa.CheckConstraint(
            "payoff_by_chapter IS NULL OR payoff_by_chapter > 0",
            name="ck_story_hook_payoff_positive",
        ),
        sa.CheckConstraint("revision > 0", name="ck_story_hook_revision_positive"),
    )
    op.create_index("ix_story_hooks_project_id", "story_hooks", ["project_id"])
    op.create_index("ix_story_hooks_source_chapter_id", "story_hooks", ["source_chapter_id"])
    op.create_index("ix_story_hooks_payoff_chapter_id", "story_hooks", ["payoff_chapter_id"])
    op.create_index(
        "ix_story_hooks_project_status_due",
        "story_hooks",
        ["project_id", "status", "payoff_by_chapter"],
    )


def downgrade() -> None:
    op.drop_index("ix_story_hooks_project_status_due", table_name="story_hooks")
    op.drop_index("ix_story_hooks_payoff_chapter_id", table_name="story_hooks")
    op.drop_index("ix_story_hooks_source_chapter_id", table_name="story_hooks")
    op.drop_index("ix_story_hooks_project_id", table_name="story_hooks")
    op.drop_table("story_hooks")
