"""
设定库模型 - 4张表
"""
from typing import TYPE_CHECKING, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from db.models_core import Project


class CodexEntry(Base, TimestampMixin):
    """设定库条目表"""

    __tablename__ = "codex_entries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # character, location, item, faction, event, rule
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    attrs: Mapped[dict] = mapped_column(JSONB, default=dict)  # 结构化属性，守卫规则前置会比对这些
    resident: Mapped[bool] = mapped_column(default=False)  # 是否常驻 layer1
    status: Mapped[str] = mapped_column(String(20), default="confirmed")  # confirmed, pending
    ref_chapters: Mapped[list[str]] = mapped_column(JSONB, default=list)  # 出现过的章节id列表
    conflicts: Mapped[list[str]] = mapped_column(JSONB, default=list)  # 冲突的 guard_issue id
    planted_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # 伏笔埋在哪一章
    expected_by: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # 期望在哪一章回收
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(1536), nullable=True)  # pgvector

    # 关系
    project: Mapped["Project"] = relationship(back_populates="codex_entries")
    aliases: Mapped[list["CodexAlias"]] = relationship(back_populates="entry", cascade="all, delete-orphan")
    relations: Mapped[list["CodexRelation"]] = relationship(
        back_populates="from_entry",
        foreign_keys="CodexRelation.from_id",
        cascade="all, delete-orphan",
    )

    __table_args__ = (Index("ix_codex_entries_embedding", "embedding", postgresql_using="hnsw"),)


class CodexAlias(Base):
    """设定库别名表 - 用于精确命中检索"""

    __tablename__ = "codex_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[str] = mapped_column(String(32), ForeignKey("codex_entries.id", ondelete="CASCADE"), index=True)
    alias: Mapped[str] = mapped_column(String(200), index=True)  # GIN 索引用于快速查找

    # 关系
    entry: Mapped["CodexEntry"] = relationship(back_populates="aliases")

    __table_args__ = (Index("ix_codex_aliases_alias_gin", "alias", postgresql_using="gin"),)


class CodexRef(Base):
    """设定库引用表 - 记录章节中引用了哪些条目"""

    __tablename__ = "codex_refs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chapter_id: Mapped[str] = mapped_column(String(32), ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    entry_id: Mapped[str] = mapped_column(String(32), ForeignKey("codex_entries.id", ondelete="CASCADE"), index=True)
    count: Mapped[int] = mapped_column(Integer, default=1)  # 在该章出现次数

    __table_args__ = (Index("ix_codex_refs_chapter_entry", "chapter_id", "entry_id", unique=True),)


class CodexRelation(Base, TimestampMixin):
    """设定库关系表 - 角色之间、地点之间的关系"""

    __tablename__ = "codex_relations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    from_id: Mapped[str] = mapped_column(String(32), ForeignKey("codex_entries.id", ondelete="CASCADE"), index=True)
    to_id: Mapped[str] = mapped_column(String(32), ForeignKey("codex_entries.id", ondelete="CASCADE"), index=True)
    relation_type: Mapped[str] = mapped_column(String(50))  # 师徒、敌对、属于、依赖等
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 关系
    from_entry: Mapped["CodexEntry"] = relationship(back_populates="relations", foreign_keys=[from_id])
