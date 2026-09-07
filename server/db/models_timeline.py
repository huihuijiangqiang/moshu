"""Author-managed story planning events."""
from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin


class TimelineEntry(Base, TimestampMixin):
    """An author-owned event independent from model-extracted story events."""

    __tablename__ = "timeline_entries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    chapter_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("chapters.id", ondelete="SET NULL"), index=True, nullable=True
    )
    timeline_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    time_text: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    story_order: Mapped[Optional[float]] = mapped_column(Numeric(24, 8), nullable=True)
    time_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    time_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    rev: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="ck_timeline_entry_status"),
        CheckConstraint("rev > 0", name="ck_timeline_entry_rev_positive"),
        CheckConstraint(
            "time_end IS NULL OR time_start IS NOT NULL",
            name="ck_timeline_entry_range_start",
        ),
        CheckConstraint(
            "time_end IS NULL OR time_end >= time_start",
            name="ck_timeline_entry_range_order",
        ),
        Index(
            "ix_timeline_entry_lane_order",
            "project_id",
            "timeline_id",
            "story_order",
        ),
    )


__all__ = ["TimelineEntry"]
