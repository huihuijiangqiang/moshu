"""
设定库模型 - 4张表
"""
from typing import TYPE_CHECKING, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from db.models_core import Project

#: CodexEntry.status 的合法取值 —— 这里是唯一定义处，CHECK 约束、API 的
#: Literal 与检索层的默认过滤都从它派生，不许各处再写一份字面量。
#:
#: 两者的区别就是架构 3 的权威层级：confirmed 是「作者确认的设定库事实」（最高
#: 权威），pending 是「模型抽取、尚未确认的候选」（第 4 级）。
CODEX_STATUSES: tuple[str, ...] = ("confirmed", "pending")

#: 检索默认只认这些 status。未确认的候选条目不得参与实体解析：一旦解析出去，
#: claim 就会按它分组，模型的猜测便以作者事实的身份进入规则判定。
CONFIRMED_CODEX_STATUSES: tuple[str, ...] = ("confirmed",)


def resolve_codex_statuses(statuses: Optional[list[str]]) -> tuple[str, ...]:
    """把调用方的 statuses 参数收敛成实际过滤用的取值。

    None 表示「按默认」，也就是只要 confirmed；要包含其他状态必须显式列出。
    空列表按错误处理 —— 没有「传空就不过滤」这种写法，否则一个空列表变量就能
    静默取消权威层级过滤。
    """
    if statuses is None:
        return CONFIRMED_CODEX_STATUSES
    if not statuses:
        raise ValueError("statuses must not be empty; pass None for the confirmed-only default")
    unknown = sorted(set(statuses) - set(CODEX_STATUSES))
    if unknown:
        raise ValueError(f"unknown codex statuses: {unknown}; expected {list(CODEX_STATUSES)}")
    return tuple(statuses)


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
    status: Mapped[str] = mapped_column(String(20), default="confirmed")  # 见 CODEX_STATUSES
    ref_chapters: Mapped[list[str]] = mapped_column(JSONB, default=list)  # 出现过的章节id列表
    conflicts: Mapped[list[str]] = mapped_column(JSONB, default=list)  # 冲突的 guard_issue id
    planted_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # 伏笔埋在哪一章
    expected_by: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # 期望在哪一章回收
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(1536), nullable=True)  # pgvector
    #: 生成当前 embedding 的那段可检索文本的 sha256（见 services.codex_embedding）。
    #: 有它才能回答两个问题：这次改动要不要重算（哈希没变就不调网关），以及哪些
    #: 条目的向量已经过时（NULL = 待重算，回填任务据此挑行）。只有写入成功才落哈希。
    embedding_text_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # 关系
    project: Mapped["Project"] = relationship(back_populates="codex_entries")
    aliases: Mapped[list["CodexAlias"]] = relationship(back_populates="entry", cascade="all, delete-orphan")
    relations: Mapped[list["CodexRelation"]] = relationship(
        back_populates="from_entry",
        foreign_keys="CodexRelation.from_id",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_codex_entries_embedding", "embedding", postgresql_using="hnsw"),
        # status 只有受控取值。API 的 Literal 是第一道防线，但直连数据库的迁移、
        # 回填脚本和后台任务绕不过 CHECK —— 一旦写进第三种状态，检索的
        # 「只认 confirmed」过滤会静默把这些条目全部排除，没人看得出原因。
        CheckConstraint(
            "status IN ('confirmed', 'pending')",
            name="ck_codex_entry_status",
        ),
    )


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
