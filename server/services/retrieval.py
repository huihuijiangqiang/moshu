"""
RAG retrieval layers for consistency checking
Layer 1: Resident codex (already in memory assembler)
Layer 2: Alias exact match
Layer 3: Vector similarity fallback
Layer 4: Adjacent chapter summaries and recent context
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_codex import CodexAlias, CodexEntry
from db.models_consistency_extended import DocumentSummary
from db.models_core import Chapter
from services.providers import EmbeddingProvider


class ConsistencyRetrieval:
    """Retrieval service for consistency checking context"""

    def __init__(self, embedding_provider: EmbeddingProvider):
        self.embedding_provider = embedding_provider

    async def resolve_entity_by_alias_l2(
        self,
        db: AsyncSession,
        project_id: str,
        text: str,
    ) -> Optional[str]:
        """
        Layer 2: 精确别名匹配（Unicode NFC 规范化）

        Args:
            db: Database session
            project_id: Project ID
            text: Entity text to resolve

        Returns:
            Entry ID if found, None otherwise
        """
        import unicodedata

        normalized = unicodedata.normalize("NFC", text.strip())

        result = await db.execute(
            select(CodexEntry.id)
            .join(CodexAlias, CodexAlias.entry_id == CodexEntry.id)
            .where(CodexEntry.project_id == project_id)
            .where(CodexAlias.alias == normalized)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def retrieve_similar_entities_l3(
        self,
        db: AsyncSession,
        project_id: str,
        query_text: str,
        top_k: int = 5,
        threshold: float = 0.8,
        kinds: Optional[list[str]] = None,
        exclude_entry_ids: Optional[list[str]] = None,
        statuses: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        Layer 3: 向量相似度召回

        Args:
            db: Database session
            project_id: Project ID
            query_text: Query text
            top_k: Number of results
            threshold: 余弦相似度下限（0~1，越大越严）
            kinds: 只召回这些 kind（character/location/item/...）
            exclude_entry_ids: 排除这些条目（例如已由 L2 精确命中的）
            statuses: 只召回这些 status（默认不限制）

        Returns:
            List of {"entry_id", "name", "kind", "distance", "similarity"}
        """
        from sqlalchemy import text

        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be between 0 and 1")
        if not query_text or not query_text.strip():
            return []

        # Generate query embedding
        query_embedding = await self.embedding_provider.embed_text(query_text)

        # pgvector cosine distance: 0 = 完全相同，1 = 正交，2 = 完全相反。
        # similarity = 1 - distance，所以 similarity >= threshold 等价于
        # distance <= 1 - threshold。
        distance_threshold = 1.0 - threshold
        distance = CodexEntry.embedding.cosine_distance(query_embedding)

        stmt = (
            select(CodexEntry.id, CodexEntry.name, CodexEntry.kind, distance.label("distance"))
            .where(CodexEntry.project_id == project_id)
            .where(CodexEntry.embedding.isnot(None))
            .where(distance <= distance_threshold)
        )
        if kinds:
            stmt = stmt.where(CodexEntry.kind.in_(kinds))
        if statuses:
            stmt = stmt.where(CodexEntry.status.in_(statuses))
        if exclude_entry_ids:
            stmt = stmt.where(CodexEntry.id.notin_(exclude_entry_ids))

        result = await db.execute(stmt.order_by(text("distance")).limit(top_k))

        candidates = []
        for row in result:
            candidates.append({
                "entry_id": row.id,
                "name": row.name,
                "kind": row.kind,
                "distance": float(row.distance),
                "similarity": 1.0 - float(row.distance),  # Convert back to similarity
            })

        return candidates

    async def resolve_entity(
        self,
        db: AsyncSession,
        project_id: str,
        text: str,
        *,
        kinds: Optional[list[str]] = None,
        threshold: float = 0.8,
    ) -> Optional[str]:
        """L2 精确别名 → L3 向量兜底，返回最可能的 entry_id。

        claim 的 subject_entry_id / object_entry_id 靠这个解析；只有精确匹配
        或高于阈值的向量召回才算命中，否则返回 None（宁可不解析也不错连）。
        """
        exact = await self.resolve_entity_by_alias_l2(db, project_id, text)
        if exact:
            return exact

        candidates = await self.retrieve_similar_entities_l3(
            db, project_id, text, top_k=1, threshold=threshold, kinds=kinds
        )
        return candidates[0]["entry_id"] if candidates else None

    async def retrieve_adjacent_summaries_l4(
        self,
        db: AsyncSession,
        chapter_id: str,
        window: int = 2,
    ) -> list[dict]:
        """
        Layer 4: 检索相邻章节摘要

        Args:
            db: Database session
            chapter_id: Current chapter ID
            window: Number of chapters before/after

        Returns:
            List of {"chapter_id": str, "title": str, "summary": str, "position": str}
        """
        # Get current chapter position (Chapter 的排序列是 idx，没有 sort_order)
        current_result = await db.execute(
            select(Chapter.idx, Chapter.project_id)
            .where(Chapter.id == chapter_id)
        )
        current = current_result.one_or_none()
        if not current:
            return []

        current_order, project_id = current

        # idx 是稀疏排序值（LexoRank 风格），不能用 idx±window 当「前后 N 章」。
        # 正确做法：按 idx 排序后取当前章前后各 window 条。
        ordered_result = await db.execute(
            select(Chapter.id, Chapter.title, Chapter.idx)
            .where(Chapter.project_id == project_id)
            .order_by(Chapter.idx)
        )
        ordered_chapters = ordered_result.all()

        position_index = next(
            (i for i, row in enumerate(ordered_chapters) if row.id == chapter_id), None
        )
        if position_index is None:
            return []

        start = max(0, position_index - window)
        end = position_index + window + 1
        adjacent_chapters = [
            row for row in ordered_chapters[start:end] if row.id != chapter_id
        ]

        results = []
        for chapter in adjacent_chapters:
            # Get latest summary
            summary_result = await db.execute(
                select(DocumentSummary.content)
                .where(DocumentSummary.owner_type == "chapter")
                .where(DocumentSummary.owner_id == chapter.id)
                .where(DocumentSummary.status == "active")
                .order_by(DocumentSummary.source_rev.desc())
                .limit(1)
            )
            summary = summary_result.scalar_one_or_none()

            position = "before" if chapter.idx < current_order else "after"
            results.append({
                "chapter_id": chapter.id,
                "title": chapter.title,
                "summary": summary or "",
                "position": position,
            })

        return results

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors"""
        import math

        dot_product = sum(x * y for x, y in zip(a, b))
        magnitude_a = math.sqrt(sum(x * x for x in a))
        magnitude_b = math.sqrt(sum(y * y for y in b))

        if magnitude_a == 0 or magnitude_b == 0:
            return 0.0

        return dot_product / (magnitude_a * magnitude_b)
