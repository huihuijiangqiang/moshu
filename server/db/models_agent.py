"""Persisted, approval-gated AI agent conversations and actions."""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from db.models_core import Project, User


class AgentSession(Base, TimestampMixin):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    status: Mapped[str] = mapped_column(String(20), default="active")

    user: Mapped["User"] = relationship()
    project: Mapped[Optional["Project"]] = relationship()

    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="ck_agent_session_status"),
    )


class AgentMessage(Base, TimestampMixin):
    __tablename__ = "agent_messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(32), ForeignKey("agent_sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    sequence: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="ck_agent_message_role"),
        Index("uq_agent_message_sequence", "session_id", "sequence", unique=True),
    )


class AgentAction(Base, TimestampMixin):
    __tablename__ = "agent_actions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(32), ForeignKey("agent_sessions.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[str] = mapped_column(String(32), ForeignKey("agent_messages.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    action_type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(200))
    parameters: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="proposed", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    executed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed', 'approved', 'running', 'succeeded', 'failed', 'rejected')",
            name="ck_agent_action_status",
        ),
        CheckConstraint(
            "action_type IN ('create_project', 'create_chapter', 'update_outline', 'create_codex_entry', 'run_consistency_scan')",
            name="ck_agent_action_type",
        ),
        Index("uq_agent_action_idempotency", "user_id", "idempotency_key", unique=True),
    )
