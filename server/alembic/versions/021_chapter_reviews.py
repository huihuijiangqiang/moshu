"""Add chapter review rounds and paragraph comments.

Revision ID: 021_chapter_reviews
Revises: 020_codex_state_changes
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "021_chapter_reviews"
down_revision: str | None = "020_codex_state_changes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chapter_review_rounds",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("chapter_id", sa.String(length=32), nullable=False),
        sa.Column("submitted_body_rev", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="submitted", nullable=False),
        sa.Column("submit_note", sa.Text(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("rev", sa.Integer(), server_default="1", nullable=False),
        sa.Column("submitted_by", sa.String(length=32), nullable=True),
        sa.Column("reviewed_by", sa.String(length=32), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('submitted', 'changes_requested', 'approved', 'superseded')",
            name="ck_chapter_review_round_status",
        ),
        sa.CheckConstraint("submitted_body_rev > 0", name="ck_chapter_review_round_body_rev_positive"),
        sa.CheckConstraint("rev > 0", name="ck_chapter_review_round_rev_positive"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["submitted_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chapter_review_rounds_project_id"), "chapter_review_rounds", ["project_id"])
    op.create_index(op.f("ix_chapter_review_rounds_chapter_id"), "chapter_review_rounds", ["chapter_id"])
    op.create_index(op.f("ix_chapter_review_rounds_submitted_by"), "chapter_review_rounds", ["submitted_by"])
    op.create_index(op.f("ix_chapter_review_rounds_reviewed_by"), "chapter_review_rounds", ["reviewed_by"])
    op.create_index(
        "uq_chapter_review_active_submission",
        "chapter_review_rounds",
        ["chapter_id"],
        unique=True,
        postgresql_where=sa.text("status = 'submitted'"),
    )
    op.create_index(
        "ix_chapter_review_round_history",
        "chapter_review_rounds",
        ["project_id", "chapter_id", "submitted_body_rev"],
    )

    op.create_table(
        "review_comments",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("round_id", sa.String(length=32), nullable=False),
        sa.Column("chapter_id", sa.String(length=32), nullable=False),
        sa.Column("body_revision", sa.Integer(), nullable=False),
        sa.Column("paragraph_id", sa.String(length=120), nullable=False),
        sa.Column("paragraph_excerpt", sa.Text(), nullable=False),
        sa.Column("selected_text", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="open", nullable=False),
        sa.Column("rev", sa.Integer(), server_default="1", nullable=False),
        sa.Column("author_id", sa.String(length=32), nullable=True),
        sa.Column("resolved_by", sa.String(length=32), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('open', 'resolved')", name="ck_review_comment_status"),
        sa.CheckConstraint("body_revision > 0", name="ck_review_comment_body_revision_positive"),
        sa.CheckConstraint("rev > 0", name="ck_review_comment_rev_positive"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["round_id"], ["chapter_review_rounds.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_review_comments_project_id"), "review_comments", ["project_id"])
    op.create_index(op.f("ix_review_comments_round_id"), "review_comments", ["round_id"])
    op.create_index(op.f("ix_review_comments_chapter_id"), "review_comments", ["chapter_id"])
    op.create_index(op.f("ix_review_comments_author_id"), "review_comments", ["author_id"])
    op.create_index("ix_review_comment_round_status", "review_comments", ["round_id", "status"])
    op.create_index("ix_review_comment_anchor", "review_comments", ["chapter_id", "body_revision", "paragraph_id"])


def downgrade() -> None:
    op.drop_index("ix_review_comment_anchor", table_name="review_comments")
    op.drop_index("ix_review_comment_round_status", table_name="review_comments")
    op.drop_index(op.f("ix_review_comments_author_id"), table_name="review_comments")
    op.drop_index(op.f("ix_review_comments_chapter_id"), table_name="review_comments")
    op.drop_index(op.f("ix_review_comments_round_id"), table_name="review_comments")
    op.drop_index(op.f("ix_review_comments_project_id"), table_name="review_comments")
    op.drop_table("review_comments")
    op.drop_index("ix_chapter_review_round_history", table_name="chapter_review_rounds")
    op.drop_index("uq_chapter_review_active_submission", table_name="chapter_review_rounds")
    op.drop_index(op.f("ix_chapter_review_rounds_reviewed_by"), table_name="chapter_review_rounds")
    op.drop_index(op.f("ix_chapter_review_rounds_submitted_by"), table_name="chapter_review_rounds")
    op.drop_index(op.f("ix_chapter_review_rounds_chapter_id"), table_name="chapter_review_rounds")
    op.drop_index(op.f("ix_chapter_review_rounds_project_id"), table_name="chapter_review_rounds")
    op.drop_table("chapter_review_rounds")
