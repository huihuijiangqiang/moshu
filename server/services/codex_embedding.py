"""
Codex 条目的 RAG 写入路径。

之前只有检索侧读 CodexEntry.embedding，却没有任何地方写入它 —— 向量列永远是
NULL，L3 向量召回等于永久返回空。这里是写入路径：

* 条目创建/更新、别名增删时按可检索文本生成向量并落库；
* 只有当可检索文本真的变化时才调用网关（省钱，也让结果稳定）；
* 批量回填按批调用 embed_batch，逐批提交。

**过时的判据是文本哈希，不是「向量为空」。** 只看 embedding IS NULL 的话，改了
描述的条目会永远带着旧向量：列不为空，回填任务看不见它，L3 于是按旧内容召回。
CodexEntry.embedding_text_hash 保存「生成当前向量的那段文本」的 sha256：

* 哈希等于当前文本 → 新鲜，不调网关；
* 哈希为 NULL     → 待重算（新建、网关失败、或调用方显式标脏）；
* 哈希不等于当前文本 → 过时（保险起见也会重算）。

只有写入成功才落哈希，所以「网关挂了」这件事本身就把条目留在待重算状态。
"""
import hashlib
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_codex import CodexAlias, CodexEntry
from services.providers import EmbeddingProvider


def build_embedding_text(
    *, name: str, kind: str, description: str, aliases: Optional[list[str]] = None
) -> str:
    """拼出用于向量化的文本。

    别名参与向量化：读者用别名指代实体时，L2 精确匹配可能落空，L3 需要能召回。
    别名去重后排序，保证同一条目每次得到同样的文本（从而同样的向量）。
    """
    parts = [f"{kind}: {name}"]
    if aliases:
        unique_aliases = sorted({alias.strip() for alias in aliases if alias and alias.strip()})
        if unique_aliases:
            parts.append("别名: " + "、".join(unique_aliases))
    if description and description.strip():
        parts.append(description.strip())
    return "\n".join(parts)


def embedding_text_hash(text: str) -> str:
    """可检索文本的稳定哈希 —— 「要不要重算」的唯一判据。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def entry_aliases(db: AsyncSession, entry_id: str) -> list[str]:
    result = await db.execute(select(CodexAlias.alias).where(CodexAlias.entry_id == entry_id))
    return list(result.scalars().all())


async def entry_embedding_text(db: AsyncSession, entry: CodexEntry) -> str:
    """条目当前的可检索文本（含别名）。"""
    return build_embedding_text(
        name=entry.name,
        kind=entry.kind,
        description=entry.description or "",
        aliases=await entry_aliases(db, entry.id),
    )


def is_embedding_fresh(entry: CodexEntry, text: str) -> bool:
    """当前向量是否就是这段文本生成的。"""
    if entry.embedding is None or entry.embedding_text_hash is None:
        return False
    return entry.embedding_text_hash == embedding_text_hash(text)


async def embed_codex_entry(
    db: AsyncSession,
    provider: EmbeddingProvider,
    entry: CodexEntry,
    *,
    force: bool = False,
) -> bool:
    """为单个条目生成并写入 embedding。

    Args:
        force: 即使文本没变也重算（换 embedding 模型时回填用）

    Returns:
        True 表示写入了新向量；False 表示已经新鲜、没调网关。
    """
    text = await entry_embedding_text(db, entry)
    if not force and is_embedding_fresh(entry, text):
        return False

    entry.embedding = await provider.embed_text(text)
    entry.embedding_text_hash = embedding_text_hash(text)
    return True


def mark_embedding_stale(entry: CodexEntry) -> None:
    """把条目标成「待重算」。

    调用方在改动可检索字段时、**与改动同一个事务**调用它。这样即使随后的网关
    调用失败（或进程直接死掉），回填任务也能凭 embedding_text_hash IS NULL 找到
    这条记录 —— 过时的向量不会永远留在库里。
    """
    entry.embedding_text_hash = None


def select_stale_entries(project_id: str):
    """待重算条目的查询：没有向量，或哈希已被标脏。

    哈希与当前文本不一致的情况在 SQL 里查不出来（文本还依赖别名表），所以改动
    路径必须调用 mark_embedding_stale 把哈希置空 —— 那才是回填能看见的信号。
    """
    return (
        select(CodexEntry)
        .where(CodexEntry.project_id == project_id)
        .where(
            or_(
                CodexEntry.embedding.is_(None),
                CodexEntry.embedding_text_hash.is_(None),
            )
        )
        .order_by(CodexEntry.id)
    )


async def embed_missing_codex_entries(
    db: AsyncSession,
    provider: EmbeddingProvider,
    *,
    project_id: str,
    batch_size: int = 32,
    commit_each_batch: bool = False,
) -> int:
    """回填项目内所有待重算的条目，返回写入条数。

    Args:
        commit_each_batch: 每批提交一次。任务重试时已完成的批次不会重做 ——
            它们的哈希已经匹配，下一轮查询直接跳过。
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    result = await db.execute(select_stale_entries(project_id))
    entries = list(result.scalars().all())
    if not entries:
        return 0

    written = 0
    for start in range(0, len(entries), batch_size):
        batch = entries[start : start + batch_size]
        texts = [await entry_embedding_text(db, entry) for entry in batch]
        vectors = await provider.embed_batch(texts)
        if len(vectors) != len(batch):
            raise RuntimeError(
                f"embedding provider returned {len(vectors)} vectors for {len(batch)} entries"
            )
        for entry, vector, text in zip(batch, vectors, texts):
            entry.embedding = vector
            entry.embedding_text_hash = embedding_text_hash(text)
            written += 1
        if commit_each_batch:
            await db.commit()

    await db.flush()
    return written


__all__ = [
    "build_embedding_text",
    "embed_codex_entry",
    "embed_missing_codex_entries",
    "embedding_text_hash",
    "entry_aliases",
    "entry_embedding_text",
    "is_embedding_fresh",
    "mark_embedding_stale",
    "select_stale_entries",
]
