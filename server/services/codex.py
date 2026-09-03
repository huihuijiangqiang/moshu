"""
设定库条目的写入服务：CRUD 与 embedding 生命周期。

**事务形状（架构 8：「锁只覆盖单章短事务，不在调用模型时持有数据库锁」）**

写入分成两个事务，顺序不能颠倒：

1. 作者的改动 + `embedding_text_hash = NULL`（标脏）在同一个短事务里提交；
2. 提交之后才调 embedding 网关，成功再用第二个事务写 `embedding` 与哈希。

这样安排的三个理由：

* 网关一次调用是几百毫秒到几秒。放在同一个事务里，就等于在等模型的整段时间里
  持着 codex_entries 的行锁 —— 架构明确禁止。
* 网关挂了不该让作者的编辑失败。条目已经落库，只是向量迟到：L3 向量召回暂时
  少一个候选，L1/L2（显式引用与精确别名）完全不受影响。
* 标脏先落库，失败就是**可见且可恢复**的状态。回填任务按
  `embedding IS NULL OR embedding_text_hash IS NULL` 扫行，进程在第 1、2 步之间
  被杀掉也不会漏 —— 反过来（先算向量再提交）则会留下「向量对不上内容」的行。

本模块的函数只 flush，不 commit：事务边界由调用方（api.codex / tasks.codex）掌握，
与 services.claim_set 同一约定。调用方的职责是在第 1 步之后真的 commit 一次，
再调 refresh_embedding_if_stale，最后为第 2 步 commit。

请求路径**不重试**：作者在等响应，重试只会把 p99 拖长。响应里的
`embedding_status="deferred"` 就是「已落库、向量待补」的显式回执，指数退避重试
由回填任务承担（见 tasks.codex）。

**「无变化不重算」的判据是可检索文本，不是「哪个字段被赋值了」。** 把描述改成
一模一样的内容、或者重复添加同一个别名，都不该产生网关调用；改了 attrs / resident
这类不参与向量化的字段同样不该。所以每次改动都比较改动前后的可检索文本，只有
真的变了才标脏。
"""
import secrets
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Optional

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_codex import CodexAlias, CodexEntry
from services.codex_embedding import (
    embed_codex_entry,
    entry_embedding_text,
    is_embedding_fresh,
    mark_embedding_stale,
    select_stale_entries,
)
from services.embedding import EmbeddingProviderError
from services.providers import EmbeddingProvider

#: CodexEntry.kind 的取值（与模型注释一致）。
CODEX_KINDS: tuple[str, ...] = ("character", "location", "item", "faction", "event", "rule")

#: 参与向量化的字段。改了它们才可能需要重算，但最终判据仍是文本比较。
EMBEDDED_FIELDS: tuple[str, ...] = ("name", "kind", "description")

#: refresh_embedding_if_stale 的三种回执。
EMBEDDING_FRESH = "fresh"
EMBEDDING_UPDATED = "updated"
EMBEDDING_DEFERRED = "deferred"

#: 视为「网关这次不可用」的异常。只吞这些：其余异常（例如维度不匹配之外的
#: 编程错误、数据库约束冲突）必须冒泡，否则会被静默降级成 deferred。
GATEWAY_FAILURES = (EmbeddingProviderError, httpx.HTTPError)


def new_entry_id() -> str:
    """与 rule_scanner 的 gi_ 前缀同一套约定。"""
    return f"cx_{secrets.token_hex(12)}"


def normalize_alias(alias: str) -> str:
    """别名规范化 —— 必须与 retrieval L2 的规范化完全一致。

    L2 精确匹配查的是 `unicodedata.normalize("NFC", text.strip())`，写入端不做
    同样的处理，含兼容字符或首尾空白的别名就永远命中不了自己。
    """
    return unicodedata.normalize("NFC", alias.strip())


def normalize_aliases(aliases: Iterable[str]) -> list[str]:
    """规范化 + 去重 + 保序。"""
    seen: set[str] = set()
    result: list[str] = []
    for alias in aliases:
        normalized = normalize_alias(alias)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


async def create_entry(
    db: AsyncSession,
    *,
    project_id: str,
    kind: str,
    name: str,
    description: str = "",
    aliases: Sequence[str] = (),
    attrs: Optional[dict] = None,
    resident: bool = False,
    status: str = "confirmed",
    entry_id: Optional[str] = None,
) -> CodexEntry:
    """创建条目及其别名。向量不在这里生成（见模块文档的两段事务）。"""
    entry = CodexEntry(
        id=entry_id or new_entry_id(),
        project_id=project_id,
        kind=kind,
        name=name,
        description=description,
        attrs=attrs if attrs is not None else {},
        resident=resident,
        status=status,
        ref_chapters=[],
        conflicts=[],
        # 新条目当然是待重算：哈希留空，网关调用失败也不会漏掉这一行。
        embedding_text_hash=None,
    )
    db.add(entry)
    await db.flush()

    for alias in normalize_aliases(aliases):
        db.add(CodexAlias(entry_id=entry.id, alias=alias))
    await db.flush()
    return entry


async def update_entry(db: AsyncSession, entry: CodexEntry, changes: dict) -> bool:
    """按字段更新条目，返回可检索文本是否变化（变化才标脏）。

    只接受 CodexEntry 上真实存在的字段；拼错字段名必须炸，不能静默丢弃。
    """
    unknown = [field for field in changes if not hasattr(CodexEntry, field)]
    if unknown:
        raise ValueError(f"unknown CodexEntry fields: {sorted(unknown)}")

    before = await entry_embedding_text(db, entry)
    for field, value in changes.items():
        setattr(entry, field, value)
    after = await entry_embedding_text(db, entry)

    changed = after != before
    if changed:
        mark_embedding_stale(entry)
    await db.flush()
    return changed


async def add_alias(db: AsyncSession, entry: CodexEntry, alias: str) -> bool:
    """添加别名，返回是否真的新增了一条。

    重复添加同一别名不产生第二行，也不重算向量 —— build_embedding_text 会去重，
    文本压根没变。
    """
    normalized = normalize_alias(alias)
    if not normalized:
        raise ValueError("alias must not be empty")

    existing = await db.execute(
        select(CodexAlias.id)
        .where(CodexAlias.entry_id == entry.id)
        .where(CodexAlias.alias == normalized)
        .limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        return False

    db.add(CodexAlias(entry_id=entry.id, alias=normalized))
    mark_embedding_stale(entry)
    await db.flush()
    return True


async def remove_alias(db: AsyncSession, entry: CodexEntry, alias: str) -> bool:
    """删除别名，返回是否真的删掉了一条（删掉才需要重算）。"""
    normalized = normalize_alias(alias)
    result = await db.execute(
        delete(CodexAlias)
        .where(CodexAlias.entry_id == entry.id)
        .where(CodexAlias.alias == normalized)
    )
    if result.rowcount == 0:
        return False

    mark_embedding_stale(entry)
    await db.flush()
    return True


async def replace_aliases(
    db: AsyncSession, entry: CodexEntry, aliases: Sequence[str]
) -> bool:
    """原子替换一个条目的全部别名，返回可检索文本是否变化。"""
    normalized = normalize_aliases(aliases)
    current = list(
        (
            await db.execute(
                select(CodexAlias.alias).where(CodexAlias.entry_id == entry.id)
            )
        ).scalars()
    )
    if set(current) == set(normalized):
        return False

    await db.execute(delete(CodexAlias).where(CodexAlias.entry_id == entry.id))
    for alias in normalized:
        db.add(CodexAlias(entry_id=entry.id, alias=alias))
    mark_embedding_stale(entry)
    await db.flush()
    return True


async def refresh_embedding_if_stale(
    db: AsyncSession,
    provider: EmbeddingProvider,
    entry: CodexEntry,
    *,
    force: bool = False,
) -> str:
    """补齐条目向量，返回 fresh / updated / deferred。

    **调用约定：进来时不能有未提交的作者写入。** 本函数只写 embedding 与哈希，
    不 commit 也不 rollback —— 调用方决定事务边界。网关失败时只 flush（确保
    ORM 对象可读），由调用方决定要不要提交标脏状态。

    网关失败不抛给调用方：条目已经落库，向量迟到只降级 L3 召回。返回 deferred，
    由回填任务带指数退避重试。
    """
    text = await entry_embedding_text(db, entry)
    if not force and is_embedding_fresh(entry, text):
        return EMBEDDING_FRESH

    try:
        await embed_codex_entry(db, provider, entry, force=True)
        await db.flush()
    except GATEWAY_FAILURES:
        # 标脏已经随作者的写入提交过了，这里只清空这半个向量。
        # 不 rollback：测试里 app_client 与 service 共用一个会话，rollback 会把
        # 会话关掉。调用方自己决定要不要提交「向量待补」这个状态。
        entry.embedding = None
        entry.embedding_text_hash = None
        await db.flush()
        return EMBEDDING_DEFERRED
    return EMBEDDING_UPDATED


async def count_stale_entries(db: AsyncSession, project_id: str) -> int:
    """项目内待重算的条目数 —— 调用时的瞬时快照，非持续失败状态。

    架构 8 要求永久失败可见。当前实现不另建 dead-letter 表、无失败次数/最后错误/
    耗尽标记，也无独立只读 GET 端点。回填任务耗尽重试后数据留在 stale，但此计数
    无法区分「首次待补」与「永久失败」，也无法持续查询。真正满足架构 8 需后续
    增加持久失败状态和专用查询接口。
    """
    subquery = select_stale_entries(project_id).subquery()
    result = await db.execute(select(func.count()).select_from(subquery))
    return int(result.scalar_one())


__all__ = [
    "CODEX_KINDS",
    "EMBEDDED_FIELDS",
    "EMBEDDING_DEFERRED",
    "EMBEDDING_FRESH",
    "EMBEDDING_UPDATED",
    "add_alias",
    "count_stale_entries",
    "create_entry",
    "new_entry_id",
    "normalize_alias",
    "normalize_aliases",
    "refresh_embedding_if_stale",
    "remove_alias",
    "update_entry",
]
