"""
核心数据模型 - 骨架（6张表）
"""
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from db.models_codex import CodexEntry


class User(Base, TimestampMixin):
    """用户表"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    phone: Mapped[Optional[str]] = mapped_column(String(20), unique=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    avatar_url: Mapped[Optional[str]] = mapped_column(String(512))
    plan: Mapped[str] = mapped_column(String(50), default="free")  # free, author, studio
    quota_remaining: Mapped[int] = mapped_column(Integer, default=0)
    quota_total: Mapped[int] = mapped_column(Integer, default=0)

    # 关系
    projects: Mapped[list["Project"]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class Project(Base, TimestampMixin):
    """项目（作品）表"""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("orgs.id", ondelete="SET NULL"), index=True, nullable=True
    )
    title: Mapped[str] = mapped_column(String(200))
    genre: Mapped[Optional[str]] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="ongoing")  # ongoing, finished, archived
    target_words_daily: Mapped[int] = mapped_column(Integer, default=3000)
    style_profile_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # 关系
    owner: Mapped["User"] = relationship(back_populates="projects")
    volumes: Mapped[list["Volume"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    chapters: Mapped[list["Chapter"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    codex_entries: Mapped[list["CodexEntry"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Volume(Base, TimestampMixin):
    """卷表"""

    __tablename__ = "volumes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    idx: Mapped[int] = mapped_column(Integer)  # 稀疏索引，步长 1024
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 卷摘要（每10章压缩一次）

    # 关系
    project: Mapped["Project"] = relationship(back_populates="volumes")
    chapters: Mapped[list["Chapter"]] = relationship(back_populates="volume", cascade="all, delete-orphan")


class Chapter(Base, TimestampMixin):
    """章节表 - 不含正文"""

    __tablename__ = "chapters"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    volume_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("volumes.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(200))
    idx: Mapped[int] = mapped_column(Integer)  # 稀疏索引，步长 1024
    words: Mapped[int] = mapped_column(Integer, default=0)
    outline: Mapped[list[str]] = mapped_column(JSONB, default=list)  # 章纲节点
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 200字章摘要

    # 关系
    project: Mapped["Project"] = relationship(back_populates="chapters")
    volume: Mapped[Optional["Volume"]] = relationship(back_populates="chapters")
    body: Mapped[Optional["ChapterBody"]] = relationship(back_populates="chapter", uselist=False)
    versions: Mapped[list["ChapterVersion"]] = relationship(back_populates="chapter", cascade="all, delete-orphan")


class ChapterBody(Base, TimestampMixin):
    """章节正文表 - 独立存储"""

    __tablename__ = "chapter_bodies"

    chapter_id: Mapped[str] = mapped_column(String(32), ForeignKey("chapters.id", ondelete="CASCADE"), primary_key=True)
    content_html: Mapped[str] = mapped_column(Text)
    content_json: Mapped[dict] = mapped_column(JSONB)  # ProseMirror JSON
    rev: Mapped[int] = mapped_column(Integer, default=1)  # 乐观锁版本号

    # 关系
    chapter: Mapped["Chapter"] = relationship(back_populates="body")


class ChapterVersion(Base):
    """章节版本快照表 - 保留最近50个"""

    __tablename__ = "chapter_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chapter_id: Mapped[str] = mapped_column(String(32), ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    content_html: Mapped[str] = mapped_column(Text)
    content_json: Mapped[dict] = mapped_column(JSONB)
    rev: Mapped[int] = mapped_column(Integer)
    trigger: Mapped[str] = mapped_column(String(50))  # manual, autosave, accept_draft
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # 关系
    chapter: Mapped["Chapter"] = relationship(back_populates="versions")
