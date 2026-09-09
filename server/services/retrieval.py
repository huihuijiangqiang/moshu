"""
RAG retrieval layers for consistency checking
Layer 1: Resident codex (already in memory assembler)
Layer 2: Alias exact match
Layer 3: Vector similarity fallback
Layer 4: Adjacent chapter summaries and recent context

**默认只召回作者已确认的条目（status='confirmed'）。** 架构 3 的权威层级把
「作者确认的设定库事实」放在第 1 级、「模型抽取尚未确认的候选」放在第 4 级；
检索层解析出的 entry_id 会被 claim 拿去分组，规则再按分组判冲突。所以一旦把
pending 条目解析出去，模型的猜测就以作者事实的身份进入了判定 —— 而且是静默的：
错连之后规则看到的是两条「同一实体」的矛盾陈述，报出来的告警指向一个作者从未
确认过的实体。

要包含其他状态必须显式传 statuses（例如设定库 UI 想给作者列出待确认候选）。
默认值不是「不过滤」：默认放开的接口，漏传参数就等于降级授权，而这个降级不会
报错 —— 取值收敛见 db.models_codex.resolve_codex_statuses。
"""
import hashlib
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_chapter_chunks import ChapterChunk
from db.models_codex import CodexAlias, CodexEntry, resolve_codex_statuses
from db.models_consistency_extended import DocumentSummary
from db.models_core import Chapter
from services.chapter_chunks import current_chunk_query
from services.providers import EmbeddingProvider

STRUCTURED_QUERY_GROUPS = ("characters", "locations", "items", "foreshadows", "scenes")
_QUERY_GROUP_ALIASES = {
    "character": "characters",
    "characters": "characters",
    "人物": "characters",
    "location": "locations",
    "locations": "locations",
    "地点": "locations",
    "item": "items",
    "items": "items",
    "物品": "items",
    "foreshadow": "foreshadows",
    "foreshadows": "foreshadows",
    "伏笔": "foreshadows",
    "scene": "scenes",
    "scenes": "scenes",
    "场景": "scenes",
}
_QUERY_GROUP_CODEX_KINDS: dict[str, tuple[str, ...]] = {
    "characters": ("character",),
    "locations": ("location",),
    "items": ("item",),
    "foreshadows": ("event",),
    # 场景不是 CodexEntry.kind；它只检索章节正文证据。
    "scenes": (),
}


def normalize_retrieval_text(value: str) -> str:
    """Build a stable comparison form without pretending to tokenize Chinese."""

    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def normalize_structured_queries(
    queries: Mapping[str, str | Sequence[str]],
    *,
    max_terms_per_group: int = 12,
) -> dict[str, list[str]]:
    """Validate and normalize structured query groups while preserving author order."""

    if max_terms_per_group <= 0:
        raise ValueError("max_terms_per_group must be positive")
    normalized: dict[str, list[str]] = {group: [] for group in STRUCTURED_QUERY_GROUPS}
    seen: dict[str, set[str]] = {group: set() for group in STRUCTURED_QUERY_GROUPS}
    for raw_group, raw_terms in queries.items():
        group = _QUERY_GROUP_ALIASES.get(str(raw_group).strip().casefold())
        if group is None:
            raise ValueError(
                f"unknown structured query group: {raw_group!r}; expected {list(STRUCTURED_QUERY_GROUPS)}"
            )
        terms: Sequence[str]
        if isinstance(raw_terms, str):
            terms = (raw_terms,)
        elif isinstance(raw_terms, Sequence):
            terms = raw_terms
        else:
            raise ValueError(f"structured query group {raw_group!r} must be a string or sequence of strings")
        for raw_term in terms:
            if not isinstance(raw_term, str):
                raise ValueError(f"structured query group {raw_group!r} contains a non-string term")
            term = unicodedata.normalize("NFKC", raw_term).strip()
            identity = normalize_retrieval_text(term)
            if not identity or identity in seen[group]:
                continue
            seen[group].add(identity)
            normalized[group].append(term)
            if len(normalized[group]) >= max_terms_per_group:
                break
    return {group: terms for group, terms in normalized.items() if terms}


def deduplicate_retrieval_results(results: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable de-duplication by source identity, persisted hash, or normalized content."""

    output: list[dict[str, Any]] = []
    seen_source_ids: set[tuple[str, str]] = set()
    seen_hashes: set[str] = set()
    seen_content: set[str] = set()
    for result in results:
        source_id = None
        if result.get("entry_id"):
            source_id = ("entry", str(result["entry_id"]))
        elif result.get("chunk_id"):
            source_id = ("chunk", str(result["chunk_id"]))
        content_hash = str(result.get("content_hash") or "").strip()
        normalized_content = normalize_retrieval_text(str(result.get("content_text") or ""))
        if source_id is not None and source_id in seen_source_ids:
            continue
        if content_hash and content_hash in seen_hashes:
            continue
        if normalized_content and normalized_content in seen_content:
            continue
        if source_id is not None:
            seen_source_ids.add(source_id)
        if content_hash:
            seen_hashes.add(content_hash)
        if normalized_content:
            seen_content.add(normalized_content)
        output.append(result)
    return output


def _content_hash(content: str) -> str:
    return hashlib.sha256(normalize_retrieval_text(content).encode("utf-8")).hexdigest()


def _codex_content(entry: CodexEntry) -> str:
    prefix = f"{entry.kind}: {entry.name}"
    description = (entry.description or "").strip()
    return f"{prefix}\n{description}" if description else prefix


class ConsistencyRetrieval:
    """Retrieval service for consistency checking context"""

    def __init__(self, embedding_provider: EmbeddingProvider):
        self.embedding_provider = embedding_provider

    async def resolve_entity_by_alias_l2(
        self,
        db: AsyncSession,
        project_id: str,
        text: str,
        statuses: list[str] | None = None,
        kinds: list[str] | None = None,
    ) -> str | None:
        """
        Layer 2: 精确别名匹配（Unicode NFC 规范化）

        Args:
            db: Database session
            project_id: Project ID
            text: Entity text to resolve
            statuses: 只召回这些 status；None = 只要 confirmed（见模块文档）
            kinds: 可选的 CodexEntry.kind 白名单

        Returns:
            Entry ID if found, None otherwise
        """
        normalized = unicodedata.normalize("NFC", text.strip())
        stmt = (
            select(CodexEntry.id)
            .join(CodexAlias, CodexAlias.entry_id == CodexEntry.id)
            .where(CodexEntry.project_id == project_id)
            .where(CodexEntry.status.in_(resolve_codex_statuses(statuses)))
            .where(CodexAlias.alias == normalized)
            .order_by(CodexEntry.id)
            .limit(1)
        )
        if kinds:
            stmt = stmt.where(CodexEntry.kind.in_(kinds))
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def _resolve_exact_entity_l2(
        self,
        db: AsyncSession,
        project_id: str,
        text: str,
        *,
        kinds: list[str] | None = None,
        statuses: list[str] | None = None,
    ) -> CodexEntry | None:
        """Resolve an exact alias first, then an exact canonical name."""

        normalized = unicodedata.normalize("NFC", text.strip())
        entry_id = await self.resolve_entity_by_alias_l2(
            db,
            project_id,
            normalized,
            statuses=statuses,
            kinds=kinds,
        )
        if entry_id is None:
            stmt = (
                select(CodexEntry.id)
                .where(
                    CodexEntry.project_id == project_id,
                    CodexEntry.status.in_(resolve_codex_statuses(statuses)),
                    CodexEntry.name == normalized,
                )
                .order_by(CodexEntry.id)
                .limit(1)
            )
            if kinds:
                stmt = stmt.where(CodexEntry.kind.in_(kinds))
            entry_id = (await db.execute(stmt)).scalar_one_or_none()
        return await db.get(CodexEntry, entry_id) if entry_id else None

    async def retrieve_similar_entities_l3(
        self,
        db: AsyncSession,
        project_id: str,
        query_text: str,
        top_k: int = 5,
        threshold: float = 0.8,
        kinds: list[str] | None = None,
        exclude_entry_ids: list[str] | None = None,
        statuses: list[str] | None = None,
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
            statuses: 只召回这些 status；None = 只要 confirmed（见模块文档）

        Returns:
            List of {"entry_id", "name", "kind", "distance", "similarity"}
        """
        from sqlalchemy import text

        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be between 0 and 1")
        effective_statuses = resolve_codex_statuses(statuses)
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
            select(
                CodexEntry.id,
                CodexEntry.name,
                CodexEntry.kind,
                CodexEntry.description,
                CodexEntry.embedding_text_hash,
                distance.label("distance"),
            )
            .where(CodexEntry.project_id == project_id)
            .where(CodexEntry.embedding.isnot(None))
            .where(CodexEntry.status.in_(effective_statuses))
            .where(distance <= distance_threshold)
        )
        if kinds:
            stmt = stmt.where(CodexEntry.kind.in_(kinds))
        if exclude_entry_ids:
            stmt = stmt.where(CodexEntry.id.notin_(exclude_entry_ids))

        result = await db.execute(stmt.order_by(text("distance")).limit(top_k))

        candidates = []
        for row in result:
            content = f"{row.kind}: {row.name}"
            if row.description and row.description.strip():
                content += f"\n{row.description.strip()}"
            similarity = 1.0 - float(row.distance)
            candidates.append(
                {
                    "entry_id": row.id,
                    "chunk_id": None,
                    "name": row.name,
                    "kind": row.kind,
                    "source": "codex_vector",
                    "reason": "semantic_similarity",
                    "score": similarity,
                    "content_text": content,
                    "content_hash": _content_hash(content),
                    "embedding_text_hash": row.embedding_text_hash,
                    "chapter_id": None,
                    "chapter_index": None,
                    "chapter_title": None,
                    "body_rev": None,
                    "revision": None,
                    "distance": float(row.distance),
                    "similarity": similarity,
                }
            )

        return candidates

    async def resolve_entity(
        self,
        db: AsyncSession,
        project_id: str,
        text: str,
        *,
        kinds: list[str] | None = None,
        threshold: float = 0.8,
        statuses: list[str] | None = None,
    ) -> str | None:
        """L2 精确别名 → L3 向量兜底，返回最可能的 entry_id。

        claim 的 subject_entry_id / object_entry_id 靠这个解析；只有精确匹配
        或高于阈值的向量召回才算命中，否则返回 None（宁可不解析也不错连）。

        两层用同一份 statuses：只在一层放开状态会让「精确别名命中不了、向量却
        命中了」这种结果出现，那比两层都不命中更难排查。
        """
        exact = await self.resolve_entity_by_alias_l2(
            db, project_id, text, statuses=statuses, kinds=kinds
        )
        if exact:
            return exact

        candidates = await self.retrieve_similar_entities_l3(
            db, project_id, text, top_k=1, threshold=threshold, kinds=kinds, statuses=statuses
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
            .where(Chapter.id == chapter_id, Chapter.deleted_at.is_(None))
        )
        current = current_result.one_or_none()
        if not current:
            return []

        current_order, project_id = current

        # idx 是稀疏排序值（LexoRank 风格），不能用 idx±window 当「前后 N 章」。
        # 正确做法：按 idx 排序后取当前章前后各 window 条。
        ordered_result = await db.execute(
            select(Chapter.id, Chapter.title, Chapter.idx)
            .where(Chapter.project_id == project_id, Chapter.deleted_at.is_(None))
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

    async def retrieve_chapter_chunks_l3(
        self,
        db: AsyncSession,
        project_id: str,
        query_text: str,
        *,
        top_k: int = 8,
        threshold: float = 0.55,
        chapter_ids: list[str] | None = None,
    ) -> list[dict]:
        """Semantic retrieval over current, ready chapter-body chunks.

        The join against ``chapter_bodies`` is deliberate: rows for historical
        body revisions remain for audit purposes, but can never be returned after
        the chapter head advances.
        """
        from sqlalchemy import text

        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be between 0 and 1")
        if not query_text or not query_text.strip():
            return []
        query_embedding = await self.embedding_provider.embed_text(query_text)
        distance = ChapterChunk.embedding.cosine_distance(query_embedding)
        stmt = current_chunk_query(project_id=project_id, chapter_ids=chapter_ids).with_only_columns(
            ChapterChunk.id,
            ChapterChunk.chapter_id,
            ChapterChunk.body_rev,
            ChapterChunk.chunk_index,
            ChapterChunk.paragraph_start,
            ChapterChunk.paragraph_end,
            ChapterChunk.paragraph_ids,
            ChapterChunk.content_text,
            ChapterChunk.content_hash,
            Chapter.idx.label("chapter_index"),
            Chapter.title.label("chapter_title"),
            distance.label("distance"),
        )
        stmt = stmt.where(distance <= 1.0 - threshold)
        result = await db.execute(stmt.order_by(text("distance")).limit(top_k))
        return [
            {
                "chunk_id": row.id,
                "chapter_id": row.chapter_id,
                "body_rev": row.body_rev,
                "chunk_index": row.chunk_index,
                "paragraph_start": row.paragraph_start,
                "paragraph_end": row.paragraph_end,
                "paragraph_ids": row.paragraph_ids or [],
                "content_text": row.content_text,
                "content_hash": row.content_hash,
                "kind": "chapter_chunk",
                "source": "chapter_chunk",
                "reason": "semantic_similarity",
                "score": 1.0 - float(row.distance),
                "chapter_index": row.chapter_index,
                "chapter_title": row.chapter_title,
                "revision": row.body_rev,
                "distance": float(row.distance),
                "similarity": 1.0 - float(row.distance),
            }
            for row in result
        ]

    async def _eligible_distant_chapter_ids(
        self,
        db: AsyncSession,
        project_id: str,
        current_chapter_id: str,
        *,
        near_window: int,
        chapter_ids: list[str] | None = None,
    ) -> list[str]:
        """Return prior chapters outside the near-context window in narrative order."""

        if near_window < 0:
            raise ValueError("near_window must not be negative")
        rows = (
            await db.execute(
                select(Chapter.id)
                .where(Chapter.project_id == project_id, Chapter.deleted_at.is_(None))
                .order_by(Chapter.idx, Chapter.id)
            )
        ).all()
        ordered_ids = [row.id for row in rows]
        try:
            current_position = ordered_ids.index(current_chapter_id)
        except ValueError:
            # An unknown or cross-project current chapter must not open the whole
            # book to retrieval, because that would include future information.
            return []
        eligible = ordered_ids[:current_position]
        if near_window:
            eligible = eligible[:-near_window] if len(eligible) > near_window else []
        if chapter_ids is not None:
            allowed = set(chapter_ids)
            eligible = [chapter_id for chapter_id in eligible if chapter_id in allowed]
        return eligible

    @staticmethod
    def _exact_codex_result(entry: CodexEntry, *, group: str, query_text: str) -> dict[str, Any]:
        content = _codex_content(entry)
        return {
            "entry_id": entry.id,
            "chunk_id": None,
            "name": entry.name,
            "kind": entry.kind,
            "source": "codex_alias",
            "reason": f"exact_name_or_alias:{group}",
            "score": 1.0,
            "content_text": content,
            "content_hash": _content_hash(content),
            "embedding_text_hash": entry.embedding_text_hash,
            "chapter_id": None,
            "chapter_index": None,
            "chapter_title": None,
            "body_rev": None,
            "revision": None,
            "distance": 0.0,
            "similarity": 1.0,
            "query_kind": group,
            "query_text": query_text,
        }

    async def retrieve_structured_context(
        self,
        db: AsyncSession,
        project_id: str,
        queries: Mapping[str, str | Sequence[str]],
        *,
        current_chapter_id: str,
        top_k: int = 12,
        near_window: int = 2,
        entity_threshold: float = 0.8,
        chunk_threshold: float = 0.55,
        chapter_ids: list[str] | None = None,
        statuses: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve explainable Codex and distant-body evidence by semantic group.

        The global result budget is divided between non-empty groups before any
        vector query runs. Each group therefore contributes at most its share;
        adding five query groups cannot silently turn ``top_k=12`` into 60 items.
        Current/future chapters and the near-context window are excluded here,
        rather than relying on every caller to remember the narrative boundary.
        Historical body revisions remain excluded by ``current_chunk_query``.
        """

        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if near_window < 0:
            raise ValueError("near_window must not be negative")
        if not 0.0 <= entity_threshold <= 1.0:
            raise ValueError("entity_threshold must be between 0 and 1")
        if not 0.0 <= chunk_threshold <= 1.0:
            raise ValueError("chunk_threshold must be between 0 and 1")
        # Validate this before an embedding request. It also keeps L2 and L3 on
        # the same author-confirmed authority boundary.
        resolve_codex_statuses(statuses)
        normalized_queries = normalize_structured_queries(queries)
        if not normalized_queries:
            return []

        eligible_chapters = await self._eligible_distant_chapter_ids(
            db,
            project_id,
            current_chapter_id,
            near_window=near_window,
            chapter_ids=chapter_ids,
        )
        active_groups = [group for group in STRUCTURED_QUERY_GROUPS if group in normalized_queries]
        base_budget, remainder = divmod(top_k, len(active_groups))
        results: list[dict[str, Any]] = []

        for position, group in enumerate(active_groups):
            group_budget = base_budget + (1 if position < remainder else 0)
            if group_budget <= 0:
                # With more active groups than result slots, deterministic group
                # order decides who gets a slot; no vector call is wasted.
                continue
            terms = normalized_queries[group]
            query_text = "\n".join(terms)
            codex_kinds = list(_QUERY_GROUP_CODEX_KINDS[group])
            candidates: list[dict[str, Any]] = []

            if codex_kinds:
                for term in terms:
                    entry = await self._resolve_exact_entity_l2(
                        db,
                        project_id,
                        term,
                        kinds=codex_kinds,
                        statuses=statuses,
                    )
                    if entry is not None:
                        candidates.append(
                            self._exact_codex_result(entry, group=group, query_text=term)
                        )
                try:
                    semantic_entities = await self.retrieve_similar_entities_l3(
                        db,
                        project_id,
                        query_text,
                        top_k=group_budget,
                        threshold=entity_threshold,
                        kinds=codex_kinds,
                        statuses=statuses,
                    )
                    for row in semantic_entities:
                        candidates.append(
                            {
                                **row,
                                "reason": f"semantic_entity:{group}",
                                "query_kind": group,
                                "query_text": query_text,
                            }
                        )
                except Exception:
                    # Exact author data remains useful when the optional vector
                    # gateway or pgvector query is temporarily unavailable.
                    pass

            if eligible_chapters:
                try:
                    semantic_chunks = await self.retrieve_chapter_chunks_l3(
                        db,
                        project_id,
                        query_text,
                        top_k=group_budget,
                        threshold=chunk_threshold,
                        chapter_ids=eligible_chapters,
                    )
                    for row in semantic_chunks:
                        candidates.append(
                            {
                                **row,
                                "reason": f"semantic_chapter:{group}",
                                "query_kind": group,
                                "query_text": query_text,
                            }
                        )
                except Exception:
                    # Same graceful degradation as the existing assembler: the
                    # caller can still use exact Codex facts and summary layers.
                    pass

            candidates.sort(
                key=lambda row: (
                    float(row.get("score") or 0.0),
                    row.get("source") == "codex_alias",
                    str(row.get("entry_id") or row.get("chunk_id") or ""),
                ),
                reverse=True,
            )
            results.extend(deduplicate_retrieval_results(candidates)[:group_budget])

        results = deduplicate_retrieval_results(results)
        results.sort(
            key=lambda row: (
                float(row.get("score") or 0.0),
                row.get("source") == "codex_alias",
                str(row.get("entry_id") or row.get("chunk_id") or ""),
            ),
            reverse=True,
        )
        return results[:top_k]

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
