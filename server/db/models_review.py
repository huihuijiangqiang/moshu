"""Chapter review rounds and paragraph-anchored editorial comments."""

from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin


class ChapterReviewRound(Base, TimestampMixin):
    """An immutable chapter-version submission and its editorial decision."""

    __tablename__ = "chapter_review_rounds"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    chapter_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chapters.id", ondelete="CASCADE"), index=True
    )
    submitted_body_rev: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="submitted", server_default="submitted")
    submit_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    decision_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rev: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    submitted_by: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reviewed_by: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('submitted', 'changes_requested', 'approved', 'superseded')",
            name="ck_chapter_review_round_status",
        ),
        CheckConstraint("submitted_body_rev > 0", name="ck_chapter_review_round_body_rev_positive"),
        CheckConstraint("rev > 0", name="ck_chapter_review_round_rev_positive"),
        Index(
            "uq_chapter_review_active_submission",
            "chapter_id",
            unique=True,
            postgresql_where=text("status = 'submitted'"),
        ),
        Index(
            "ix_chapter_review_round_history",
            "project_id",
            "chapter_id",
            "submitted_body_rev",
        ),
    )


class ReviewComment(Base, TimestampMixin):
    """Editorial feedback anchored to a paragraph in the submitted snapshot."""

    __tablename__ = "review_comments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    round_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chapter_review_rounds.id", ondelete="CASCADE"), index=True
    )
    chapter_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chapters.id", ondelete="CASCADE"), index=True
    )
    body_revision: Mapped[int] = mapped_column(Integer)
    paragraph_id: Mapped[str] = mapped_column(String(120))
    paragraph_excerpt: Mapped[str] = mapped_column(Text)
    selected_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open", server_default="open")
    rev: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    author_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    resolved_by: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('open', 'resolved')", name="ck_review_comment_status"),
        CheckConstraint("body_revision > 0", name="ck_review_comment_body_revision_positive"),
        CheckConstraint("rev > 0", name="ck_review_comment_rev_positive"),
        Index("ix_review_comment_round_status", "round_id", "status"),
        Index("ix_review_comment_anchor", "chapter_id", "body_revision", "paragraph_id"),
    )
