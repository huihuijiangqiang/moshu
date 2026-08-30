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
    ) -> list[dict]:
        """
        Layer 3: 向量相似度召回

        Args:
            db: Database session
            project_id: Project ID
            query_text: Query text
            top_k: Number of results
            threshold: Similarity threshold (cosine distance, lower is better)

        Returns:
            List of {"entry_id": str, "name": str, "distance": float}
        """
        from sqlalchemy import text

        # Generate query embedding
        query_embedding = await self.embedding_provider.embed_text(query_text)

        # pgvector cosine distance search using <=> operator
        # Distance ranges from 0 (identical) to 2 (opposite)
        # Threshold of 0.8 similarity ≈ 0.4 distance
        distance_threshold = 1.0 - threshold

        result = await db.execute(
            select(
                CodexEntry.id,
                CodexEntry.name,
                CodexEntry.embedding.cosine_distance(query_embedding).label("distance"),
            )
            .where(CodexEntry.project_id == project_id)
            .where(CodexEntry.embedding.isnot(None))
            .where(CodexEntry.embedding.cosine_distance(query_embedding) <= distance_threshold)
            .order_by(text("distance"))
            .limit(top_k)
        )

        candidates = []
        for row in result:
            candidates.append({
                "entry_id": row.id,
                "name": row.name,
                "distance": float(row.distance),
                "similarity": 1.0 - float(row.distance),  # Convert back to similarity
            })

        return candidates

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
        # Get current chapter position
        current_result = await db.execute(
            select(Chapter.sort_order, Chapter.project_id)
            .where(Chapter.id == chapter_id)
        )
        current = current_result.one_or_none()
        if not current:
            return []

        current_order, project_id = current

        # Get adjacent chapters
        adjacent_result = await db.execute(
            select(Chapter.id, Chapter.title, Chapter.sort_order)
            .where(Chapter.project_id == project_id)
            .where(Chapter.sort_order >= current_order - window)
            .where(Chapter.sort_order <= current_order + window)
            .where(Chapter.id != chapter_id)
            .order_by(Chapter.sort_order)
        )
        adjacent_chapters = adjacent_result.all()

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

            position = "before" if chapter.sort_order < current_order else "after"
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
