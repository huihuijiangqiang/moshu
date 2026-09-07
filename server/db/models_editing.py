"""Persistent editing operations that span multiple chapters."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin


class TextReplacementRun(Base, TimestampMixin):
    """One atomic multi-chapter find/replace operation and its undo boundary."""

    __tablename__ = "text_replacement_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="applied", index=True)
    query_text: Mapped[str] = mapped_column(Text)
    replacement_text: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String(20))
    case_sensitive: Mapped[bool] = mapped_column(Boolean, default=True)
    total_matches: Mapped[int] = mapped_column(Integer)
    affected_chapters: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    undone_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('applied', 'undone')", name="ck_text_replacement_run_status"),
        CheckConstraint("scope IN ('chapter', 'volume', 'project')", name="ck_text_replacement_run_scope"),
        CheckConstraint("total_matches > 0", name="ck_text_replacement_run_matches_positive"),
    )
