"""
组织权限模型 - 3张表（MVP建表不开功能）
"""
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from db.models_core import Chapter, User


class Org(Base, TimestampMixin):
    """组织表"""

    __tablename__ = "orgs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    plan: Mapped[str] = mapped_column(String(50), default="studio")  # studio, enterprise
    seats: Mapped[int] = mapped_column(Integer, default=5)
    seats_used: Mapped[int] = mapped_column(Integer, default=0)

    # 关系
    members: Mapped[list["OrgMember"]] = relationship(back_populates="org", cascade="all, delete-orphan")


class OrgMember(Base, TimestampMixin):
    """组织成员表"""

    __tablename__ = "org_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[str] = mapped_column(String(32), ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # owner, lead, writer, editor, viewer

    # 关系
    org: Mapped["Org"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship()

    __table_args__ = (UniqueConstraint("org_id", "user_id", name="uq_org_user"),)


class ChapterAssignment(Base, TimestampMixin):
    """章节分派表"""

    __tablename__ = "chapter_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chapter_id: Mapped[str] = mapped_column(String(32), ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    assigned_to: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    assigned_by: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="assigned")  # assigned, claimed, returned, completed
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # 关系
    chapter: Mapped["Chapter"] = relationship()
    assignee: Mapped["User"] = relationship(foreign_keys=[assigned_to])
