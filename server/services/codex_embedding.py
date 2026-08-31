"""
Codex 条目的 RAG 写入路径。

之前只有检索侧读 CodexEntry.embedding，却没有任何地方写入它 —— 向量列永远是
NULL，L3 向量召回等于永久返回空。这里补上写入路径：

* 条目创建/更新时根据可检索文本生成向量并落库；
* 只有当可检索文本真的变化时才重新调用网关（省钱、也让结果稳定）；
* 批量回填时按批调用 embed_batch，并逐条落库。
"""
from typing import Optional

from sqlalchemy import select
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


async def _entry_aliases(db: AsyncSession, entry_id: str) -> list[str]:
    result = await db.execute(select(CodexAlias.alias).where(CodexAlias.entry_id == entry_id))
    return list(result.scalars().all())


async def embed_codex_entry(
    db: AsyncSession,
    provider: EmbeddingProvider,
    entry: CodexEntry,
    *,
    force: bool = False,
) -> bool:
    """为单个条目生成并写入 embedding。

    Args:
        force: 即使已有向量也重算（模型换代时回填用）

    Returns:
        True 表示写入了新向量；False 表示已有向量且无需重算。
    """
    if entry.embedding is not None and not force:
        return False

    text = build_embedding_text(
        name=entry.name,
        kind=entry.kind,
        description=entry.description or "",
        aliases=await _entry_aliases(db, entry.id),
    )
    entry.embedding = await provider.embed_text(text)
    return True


async def embed_missing_codex_entries(
    db: AsyncSession,
    provider: EmbeddingProvider,
    *,
    project_id: str,
    batch_size: int = 32,
) -> int:
    """回填项目内所有缺向量的条目，返回写入条数。"""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    result = await db.execute(
        select(CodexEntry)
        .where(CodexEntry.project_id == project_id)
        .where(CodexEntry.embedding.is_(None))
        .order_by(CodexEntry.id)
    )
    entries = list(result.scalars().all())
    if not entries:
        return 0

    written = 0
    for start in range(0, len(entries), batch_size):
        batch = entries[start : start + batch_size]
        texts = [
            build_embedding_text(
                name=entry.name,
                kind=entry.kind,
                description=entry.description or "",
                aliases=await _entry_aliases(db, entry.id),
            )
            for entry in batch
        ]
        vectors = await provider.embed_batch(texts)
        if len(vectors) != len(batch):
            raise RuntimeError(
                f"embedding provider returned {len(vectors)} vectors for {len(batch)} entries"
            )
        for entry, vector in zip(batch, vectors):
            entry.embedding = vector
            written += 1

    await db.flush()
    return written
