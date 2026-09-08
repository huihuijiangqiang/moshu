"""Scene cards used to plan a chapter without replacing legacy outline nodes."""

from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin


class ChapterScene(Base, TimestampMixin):
    """A stable, revisioned scene card belonging to one novel chapter.

    ``outline_rev`` and ``body_rev`` are the chapter snapshots against which the
    card was last saved.  They deliberately do not mutate the legacy string
    outline, allowing imported projects to adopt cards incrementally.
    """

    __tablename__ = "chapter_scenes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    chapter_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    pov_entry_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("codex_entries.id", ondelete="SET NULL"), nullable=True
    )
    location_entry_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("codex_entries.id", ondelete="SET NULL"), nullable=True
    )
    goal: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    obstacle: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    turn: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    info_gain: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    emotion_shift: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    hook: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="planning", server_default="planning", nullable=False)
    rev: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    outline_rev: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    body_rev: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("chapter_id", "order", name="uq_chapter_scene_order"),
        CheckConstraint('"order" > 0', name="ck_chapter_scene_order_positive"),
        CheckConstraint("rev > 0", name="ck_chapter_scene_rev_positive"),
        CheckConstraint("outline_rev >= 0", name="ck_chapter_scene_outline_rev_nonnegative"),
        CheckConstraint("body_rev IS NULL OR body_rev >= 0", name="ck_chapter_scene_body_rev_nonnegative"),
        CheckConstraint(
            "status IN ('planning', 'ready', 'written', 'needs_revision', 'archived')",
            name="ck_chapter_scene_status",
        ),
    )
