"""
claim 的实体链接（entry_id 解析）。

抽取出来的 claim 只有文本（subject_text / object_value），必须解析成
codex_entries.id 才能被规则使用 —— alive/ownership/knowledge 三条 P0 规则都按
subject_entry_id、object_entry_id 分组。抽取步骤原先根本不解析，两个字段永远是
NULL，于是「李长风」和「李长风」在两章里被当成两个无关文本，跨章冲突检测形同虚设。

解析顺序沿用检索层的分层设计：
L2 精确别名（确定性、无网络） → L3 向量兜底（需要 embedding 网关 + pgvector）。

两层都只认作者已确认的条目（retrieval 的默认）。抽取出的 claim 会按 entry_id
分组进规则判定，把未确认的候选条目连进去，等于让模型的猜测取得作者事实的权威
（架构 3）。要放开状态必须显式构造 statuses 传进来。

向量兜底是可降级的：网关或 pgvector 不可用时不应让整个抽取失败（entry_id 可空，
少解析出来的只是少一层召回），但也不能静默吞掉 —— 失败次数会计入 stats 并随任务
结果返回，便于监控发现「链接长期全靠别名」。
"""
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from services.retrieval import ConsistencyRetrieval

#: object_type='entity' 时才把 object_value 当作实体去解析；
#: scalar/timestamp 之类的值不是实体，解析只会错连。
ENTITY_OBJECT_TYPES = frozenset({"entity", "location"})

#: object_type -> 允许命中的 codex kind，避免把地点连到角色上。
_OBJECT_KIND_HINTS = {
    "location": ["location", "place"],
}


@dataclass
class LinkStats:
    """一次抽取里实体链接的统计，用于观测降级情况。"""

    resolved_subjects: int = 0
    unresolved_subjects: int = 0
    resolved_objects: int = 0
    vector_failures: int = 0
    vector_error_samples: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "resolved_subjects": self.resolved_subjects,
            "unresolved_subjects": self.unresolved_subjects,
            "resolved_objects": self.resolved_objects,
            "vector_failures": self.vector_failures,
            "vector_error_samples": list(self.vector_error_samples),
        }


class EntityLinker:
    """把 claim 文本解析成 codex entry_id，同一批抽取内做缓存。

    缓存是必需的：一章里同一个角色名会出现在几十条 claim 上，不缓存就会对同一
    文本反复调用 embedding 网关。
    """

    def __init__(
        self,
        retrieval: ConsistencyRetrieval,
        *,
        threshold: float = 0.82,
        use_vector_fallback: bool = True,
        strict: bool = False,
    ):
        """
        Args:
            retrieval: 检索服务（提供 L2/L3）
            threshold: L3 相似度下限，低于此值宁可不解析也不错连
            use_vector_fallback: 关掉后只用精确别名（没有 pgvector 的环境）
            strict: True 时向量层异常直接抛出，False 时降级并计数
        """
        self._retrieval = retrieval
        self._threshold = threshold
        self._use_vector_fallback = use_vector_fallback
        self._strict = strict
        self._cache: dict[tuple[str, str, Optional[str]], Optional[str]] = {}
        self.stats = LinkStats()

    @property
    def usage_events(self) -> list[dict]:
        """Expose vector-fallback calls for the platform cost ledger."""
        provider = self._retrieval.embedding_provider
        events = getattr(provider, "usage_events", None)
        return list(events) if isinstance(events, list) else []

    @staticmethod
    def _cache_key(project_id: str, text: str, kinds: Optional[list[str]]) -> tuple:
        normalized = unicodedata.normalize("NFC", text.strip()).lower()
        return (project_id, normalized, ",".join(sorted(kinds)) if kinds else None)

    async def resolve(
        self,
        db: AsyncSession,
        project_id: str,
        text: Optional[str],
        *,
        kinds: Optional[list[str]] = None,
    ) -> Optional[str]:
        """解析单个文本；命中不了返回 None。"""
        if not text or not text.strip():
            return None

        key = self._cache_key(project_id, text, kinds)
        if key in self._cache:
            return self._cache[key]

        entry_id = await self._retrieval.resolve_entity_by_alias_l2(db, project_id, text)

        if entry_id is None and self._use_vector_fallback:
            try:
                candidates = await self._retrieval.retrieve_similar_entities_l3(
                    db,
                    project_id,
                    text,
                    top_k=1,
                    threshold=self._threshold,
                    kinds=kinds,
                )
                entry_id = candidates[0]["entry_id"] if candidates else None
            except Exception as exc:
                if self._strict:
                    raise
                # 向量层不可用（网关故障 / 无 pgvector）时降级为「只用别名」，
                # 但记账，避免长期静默退化。
                self.stats.vector_failures += 1
                if len(self.stats.vector_error_samples) < 3:
                    self.stats.vector_error_samples.append(f"{type(exc).__name__}: {exc}")
                entry_id = None

        self._cache[key] = entry_id
        return entry_id

    async def link_claim(
        self,
        db: AsyncSession,
        project_id: str,
        claim_data: dict,
    ) -> tuple[Optional[str], Optional[str]]:
        """解析一条 claim 的 (subject_entry_id, object_entry_id)。"""
        return (await self.link_claims(db, project_id, [claim_data]))[0]

    async def _resolve_many(
        self,
        db: AsyncSession,
        project_id: str,
        requests: list[tuple[Optional[str], Optional[list[str]]]],
    ) -> list[Optional[str]]:
        """Resolve aliases first, then batch the vector fallback by kind."""
        results: list[Optional[str]] = [None] * len(requests)
        pending: dict[tuple[str, str, Optional[str]], tuple[str, Optional[list[str]], list[int]]] = {}

        for index, (text, kinds) in enumerate(requests):
            if not text or not text.strip():
                continue
            key = self._cache_key(project_id, text, kinds)
            if key in self._cache:
                results[index] = self._cache[key]
                continue
            item = pending.get(key)
            if item is None:
                pending[key] = (text, kinds, [index])
            else:
                item[2].append(index)

        unresolved: list[tuple[str, Optional[list[str]], tuple[str, str, Optional[str]], list[int]]] = []
        for key, (text, kinds, indexes) in pending.items():
            entry_id = await self._retrieval.resolve_entity_by_alias_l2(
                db, project_id, text, kinds=kinds
            )
            if entry_id:
                self._cache[key] = entry_id
                for index in indexes:
                    results[index] = entry_id
            else:
                unresolved.append((text, kinds, key, indexes))

        if not unresolved or not self._use_vector_fallback:
            return results

        batch_method = getattr(self._retrieval, "retrieve_similar_entities_l3_batch", None)
        can_batch = callable(batch_method) and self._retrieval.embedding_provider is not None
        if not can_batch:
            for text, kinds, key, indexes in unresolved:
                try:
                    candidates = await self._retrieval.retrieve_similar_entities_l3(
                        db, project_id, text, top_k=1, threshold=self._threshold, kinds=kinds
                    )
                    entry_id = candidates[0]["entry_id"] if candidates else None
                except Exception as exc:
                    if self._strict:
                        raise
                    self._record_vector_failure(exc)
                    entry_id = None
                self._cache[key] = entry_id
                for index in indexes:
                    results[index] = entry_id
            return results

        grouped: dict[tuple[str, ...], list[tuple[str, Optional[list[str]], tuple[str, str, Optional[str]], list[int]]]] = {}
        for item in unresolved:
            grouped.setdefault(tuple(item[1] or ()), []).append(item)

        for grouped_items in grouped.values():
            kinds = grouped_items[0][1]
            for start in range(0, len(grouped_items), settings.embedding_batch_size):
                batch = grouped_items[start : start + settings.embedding_batch_size]
                try:
                    candidate_batches = await batch_method(
                        db,
                        project_id,
                        [item[0] for item in batch],
                        top_k=1,
                        threshold=self._threshold,
                        kinds=kinds,
                    )
                    if len(candidate_batches) != len(batch):
                        raise RuntimeError("embedding batch result length mismatch")
                except Exception as exc:
                    if self._strict:
                        raise
                    self._record_vector_failure(exc, count=len(batch))
                    candidate_batches = [[] for _ in batch]

                for item, candidates in zip(batch, candidate_batches):
                    entry_id = candidates[0]["entry_id"] if candidates else None
                    self._cache[item[2]] = entry_id
                    for index in item[3]:
                        results[index] = entry_id

        return results

    def _record_vector_failure(self, error: Exception, *, count: int = 1) -> None:
        self.stats.vector_failures += count
        if len(self.stats.vector_error_samples) < 3:
            self.stats.vector_error_samples.append(f"{type(error).__name__}: {error}")

    async def link_claims(
        self,
        db: AsyncSession,
        project_id: str,
        claims: list[dict],
    ) -> list[tuple[Optional[str], Optional[str]]]:
        """Link a claim batch while sharing alias and embedding work."""
        requests: list[tuple[Optional[str], Optional[list[str]]]] = []
        subject_indexes: list[int] = []
        object_indexes: list[Optional[int]] = []
        for claim in claims:
            subject_indexes.append(len(requests))
            requests.append((claim.get("subject_text"), None))
            object_type = claim.get("object_type")
            if object_type in ENTITY_OBJECT_TYPES:
                object_indexes.append(len(requests))
                requests.append((claim.get("object_value"), _OBJECT_KIND_HINTS.get(object_type)))
            else:
                object_indexes.append(None)

        resolved = await self._resolve_many(db, project_id, requests)
        linked: list[tuple[Optional[str], Optional[str]]] = []
        for index, object_index in zip(subject_indexes, object_indexes):
            subject_entry_id = resolved[index]
            object_entry_id = resolved[object_index] if object_index is not None else None
            if subject_entry_id:
                self.stats.resolved_subjects += 1
            else:
                self.stats.unresolved_subjects += 1
            if object_entry_id:
                self.stats.resolved_objects += 1
            linked.append((subject_entry_id, object_entry_id))
        return linked


__all__ = ["ENTITY_OBJECT_TYPES", "EntityLinker", "LinkStats"]
