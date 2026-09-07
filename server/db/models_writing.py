"""Daily writing progress aggregates used by the bookshelf and project header."""

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class ProjectDailyWriting(Base):
    """Net positive words saved per chapter and UTC calendar day.

    Keeping one row per chapter avoids a hot project-wide counter and lets the
    chapter row lock serialize concurrent saves for the same writing stream.
    Deletions are intentionally not counted as negative progress.
    """

    __tablename__ = "project_daily_writing"

    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    chapter_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chapters.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    words_added: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    saves: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("words_added >= 0", name="ck_project_daily_writing_words_nonnegative"),
        CheckConstraint("saves >= 0", name="ck_project_daily_writing_saves_nonnegative"),
        Index("ix_project_daily_writing_project_day", "project_id", "day"),
    )


__all__ = ["ProjectDailyWriting"]
