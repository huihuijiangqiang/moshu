"""Platform positioning cards for a novel project.

The current card is mutable with an integer optimistic-lock revision.  Every
successful update also writes an immutable snapshot so authors can inspect or
restore previous positioning decisions without replacing the whole project
settings JSON document.
"""

from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin


PLATFORM_VALUES = ("fanqie", "qimao", "qidian", "general")
POSITIONING_STATUSES = ("draft", "active", "archived")


class ProjectPositioning(Base, TimestampMixin):
    """The current platform promise card for one project."""

    __tablename__ = "project_positionings"
    __table_args__ = (
        UniqueConstraint("project_id", name="uq_project_positioning_project"),
        CheckConstraint(
            "platform IN ('fanqie', 'qimao', 'qidian', 'general')",
            name="ck_project_positioning_platform",
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'archived')",
            name="ck_project_positioning_status",
        ),
        CheckConstraint("revision >= 0", name="ck_project_positioning_revision_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False, server_default="general")
    title_candidates: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    selling_point: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    synopsis: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    protagonist_dilemma: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    first_payoff: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    long_term_arc: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="draft")

    project: Mapped["Project"] = relationship(back_populates="positioning")
    revisions: Mapped[list["ProjectPositioningRevision"]] = relationship(
        back_populates="positioning", cascade="all, delete-orphan"
    )


class ProjectPositioningRevision(Base):
    """Immutable snapshot of a positioning card after each successful update."""

    __tablename__ = "project_positioning_revisions"
    __table_args__ = (
        UniqueConstraint("positioning_id", "revision", name="uq_project_positioning_revision"),
        Index("ix_project_positioning_revisions_project_id", "project_id"),
        CheckConstraint("revision > 0", name="ck_project_positioning_revision_positive"),
        CheckConstraint(
            "platform IN ('fanqie', 'qimao', 'qidian', 'general')",
            name="ck_project_positioning_revision_platform",
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'archived')",
            name="ck_project_positioning_revision_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    positioning_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("project_positionings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    title_candidates: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    selling_point: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    synopsis: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    protagonist_dilemma: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    first_payoff: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    long_term_arc: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    positioning: Mapped[ProjectPositioning] = relationship(back_populates="revisions")
