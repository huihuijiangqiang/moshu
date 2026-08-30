"""
守卫模型 - 2张表
"""
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, String, Text
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
    entry_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("codex_entries.id", ondelete="SET NULL"), nullable=True
    )
    issue_type: Mapped[str] = mapped_column(String(50))  # conflict, foreshadow_overdue, attribute_mismatch
    severity: Mapped[str] = mapped_column(String(20), default="medium")  # low, medium, high
    description: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSONB)  # 证据：前后文、冲突字段等
    anchor: Mapped[dict] = mapped_column(JSONB)  # {pid: str, start: int, end: int}
    actions: Mapped[list[str]] = mapped_column(JSONB)  # ['keep-old', 'keep-new', 'intentional']
    resolved: Mapped[bool] = mapped_column(default=False)
    resolution: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # 用户选择的处置方式
    false_positive: Mapped[bool] = mapped_column(default=False)  # 误报标记，回流评测集

    # 关系
    project: Mapped["Project"] = relationship()
    chapter: Mapped["Chapter"] = relationship()


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
