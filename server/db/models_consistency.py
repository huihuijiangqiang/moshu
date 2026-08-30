"""
一致性相关数据模型 - P0-A 持久化基础设施
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin


class ChapterOutlineState(Base, TimestampMixin):
    """章纲当前状态 - 与 chapters 一对一，避免修改 models_core.py"""

    __tablename__ = "chapter_outline_states"

    chapter_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chapters.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    body_needs_revision: Mapped[bool] = mapped_column(default=False, nullable=False)
    marked_outline_rev: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    marked_body_rev: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class ChapterOutlineRevision(Base):
    """章纲历史快照 - 不可变"""

    __tablename__ = "chapter_outline_revisions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chapter_id: Mapped[str] = mapped_column(String(32), ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    nodes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    body_policy: Mapped[str] = mapped_column(String(32), nullable=False)
    body_rev_at_change: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default="now()")

    __table_args__ = (
        UniqueConstraint("chapter_id", "revision", name="uq_chapter_outline_revision"),
        CheckConstraint(
            "body_policy IN ('plan_only', 'mark_body_for_revision')",
            name="ck_body_policy_enum",
        ),
    )


class OutboxEvent(Base, TimestampMixin):
    """事务 outbox 事件 - 保证异步任务至少投递一次"""

    __tablename__ = "outbox_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    topic: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_rev: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(server_default="now()")
    lease_owner: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    lease_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("topic", "aggregate_id", "aggregate_rev", name="uq_outbox_event_key"),
        CheckConstraint(
            "status IN ('pending', 'dispatching', 'sent', 'failed', 'dead_letter')",
            name="ck_outbox_status_enum",
        ),
    )


class IdempotencyRecord(Base):
    """幂等键记录 - 保证 API 请求幂等性"""

    __tablename__ = "idempotency_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(100), nullable=False)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default="now()")

    __table_args__ = (UniqueConstraint("scope", "key", name="uq_idempotency_scope_key"),)
