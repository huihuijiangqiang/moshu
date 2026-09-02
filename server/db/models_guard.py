"""
守卫模型 - 2张表
"""
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from db.models_codex import CodexEntry
    from db.models_core import Chapter, Project


class GuardIssue(Base, TimestampMixin):
    """一致性守卫问题表"""

    __tablename__ = "guard_issues"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[str] = mapped_column(String(32), ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("consistency_runs.id", ondelete="CASCADE"), index=True)
    entry_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("codex_entries.id", ondelete="SET NULL"), nullable=True
    )
    issue_type: Mapped[str] = mapped_column(String(50), index=True)
    rule_version: Mapped[str] = mapped_column(String(50), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    description: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSONB)
    anchor: Mapped[dict] = mapped_column(JSONB)
    actions: Mapped[list[str]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), server_default="open", nullable=False, index=True)
    issue_rev: Mapped[int] = mapped_column(Integer, server_default="1", nullable=False)
    resolved: Mapped[bool] = mapped_column(default=False)
    resolution: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    false_positive: Mapped[bool] = mapped_column(default=False)
    stale_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    arbitration_status: Mapped[str] = mapped_column(
        String(20), server_default="not_requested", nullable=False, index=True
    )
    arbitration_confidence: Mapped[Optional[float]] = mapped_column(
        Numeric(4, 3), nullable=True
    )
    arbitration_rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    arbitration_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    arbitration_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    arbitration_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    arbitrated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 关系
    project: Mapped["Project"] = relationship()
    chapter: Mapped["Chapter"] = relationship()

    __table_args__ = (
        UniqueConstraint("project_id", "fingerprint", name="uq_guard_issue_fingerprint"),
        CheckConstraint("status IN ('open', 'resolved', 'stale', 'false_positive')", name="ck_guard_issue_status"),
        CheckConstraint("severity IN ('low', 'medium', 'high')", name="ck_guard_issue_severity"),
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="ck_guard_issue_confidence"),
        CheckConstraint(
            "arbitration_status IN "
            "('not_requested', 'pending', 'supported', 'unsupported', 'uncertain', 'failed')",
            name="ck_guard_issue_arbitration_status",
        ),
        CheckConstraint(
            "arbitration_confidence IS NULL OR "
            "(arbitration_confidence >= 0.0 AND arbitration_confidence <= 1.0)",
            name="ck_guard_issue_arbitration_confidence",
        ),
        CheckConstraint("issue_rev > 0", name="ck_guard_issue_rev_positive"),
        Index("ix_guard_issue_status_stale", "status", "stale_at"),
        Index("ix_guard_issue_run_arbitration", "run_id", "arbitration_status"),
    )


class Foreshadow(Base, TimestampMixin):
    """伏笔登记表"""

    __tablename__ = "foreshadows"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    entry_id: Mapped[str] = mapped_column(String(32), ForeignKey("codex_entries.id", ondelete="CASCADE"), index=True)
    planted_chapter_id: Mapped[str] = mapped_column(String(32), ForeignKey("chapters.id", ondelete="CASCADE"))
    expected_chapter_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    description: Mapped[str] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(default=False)
    resolved_chapter_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # 关系
    project: Mapped["Project"] = relationship()
    entry: Mapped["CodexEntry"] = relationship()
