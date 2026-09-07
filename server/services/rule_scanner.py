"""
Rule Scanner - Persistent consistency checking with fingerprinted issues

设计要点（都是踩过的坑）：

* 指纹必须语义稳定。早期实现把 claim 的行 id 拼进指纹，而每次重新抽取都会插入
  新的 claim 行 —— 指纹随之改变，同一个冲突每次扫描都被当成新 issue，旧 issue
  被标成 stale，作者的处置记录形同失效。现在指纹只由「冲突类型 + 主体 + 时间线
  + 对象」构成。
* 锚点必须能定位到出问题的那一版正文。只写 paragraph_id 不够：段落 id 在不同
  body_rev 下可能复用或漂移，前端无法确定该去哪一版找。锚点现在带上
  chapter_id / body_rev。
* actions 必须是 GuardResolution CHECK 约束允许的值，否则前端照着 actions 提交
  处置会被 422 拒掉。
* 已被判为误报的 issue 不得因为再次命中就复活。
* 相同实质证据的重复扫描必须完全幂等：不增 issue_rev、不删写 evidence。只有
  claim ids / body_rev / source_anchor 等实质证据变化才 issue_rev+1 并重开。
* resolved 和 stale 重现按同样的「证据是否变化」规则处理。
"""
import hashlib
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_codex import CodexEntry
from db.models_consistency_extended import ConsistencyClaim, GuardIssueEvidence
from db.models_core import Chapter
from db.models_guard import Foreshadow, GuardIssue

logger = logging.getLogger(__name__)

#: 每类冲突可供作者选择的处置动作，取值必须落在 GuardResolution 的 CHECK 约束内。
ISSUE_ACTIONS: dict[str, list[str]] = {
    "alive_conflict": ["accept_old_fact", "accept_new_fact", "intentional_exception", "false_positive"],
    "ownership_conflict": ["accept_old_fact", "accept_new_fact", "intentional_exception", "false_positive"],
    "knowledge_boundary": ["accept_new_fact", "intentional_exception", "fixed_in_body", "false_positive"],
    "timeline_conflict": ["accept_old_fact", "accept_new_fact", "intentional_exception", "false_positive"],
    "ability_boundary": ["accept_new_fact", "intentional_exception", "fixed_in_body", "false_positive"],
    "location_conflict": ["accept_old_fact", "accept_new_fact", "intentional_exception", "false_positive"],
    "foreshadow_overdue": ["fixed_in_body", "intentional_exception", "defer", "false_positive"],
}
DEFAULT_ACTIONS = ["accept_old_fact", "accept_new_fact", "intentional_exception", "false_positive"]

IMPACT_PREDICATE_FAMILIES = {
    "alive": frozenset({"alive"}),
    "owns": frozenset({"owns"}),
    "uses_knowledge": frozenset({"uses_knowledge", "acquires_knowledge"}),
    "acquires_knowledge": frozenset({"uses_knowledge", "acquires_knowledge"}),
    "uses_ability": frozenset({"uses_ability", "acquires_ability", "has_ability"}),
    "acquires_ability": frozenset({"uses_ability", "acquires_ability", "has_ability"}),
    "has_ability": frozenset({"uses_ability", "acquires_ability", "has_ability"}),
    "located_at": frozenset({"located_at"}),
}
MAX_IMPACT_KEYS = 500


def claim_anchor(claim: ConsistencyClaim) -> dict[str, Any]:
    """定位到某条 claim 出处的锚点：章节 + 正文版本 + 段落。"""
    return {
        "chapter_id": claim.chapter_id,
        "body_rev": claim.body_rev,
        "pid": claim.paragraph_id,
        "claim_id": claim.id,
    }


class RuleScanner:
    """Scans accepted claims and produces fingerprinted GuardIssue records"""

    def __init__(self, rule_version: str = "v1.0"):
        self.rule_version = rule_version
        self.last_scan_claim_count = 0
        self.last_scan_pruned_count = 0
        self.last_scan_interval_filter_applied = False
        self.last_scan_interval_filter_families: tuple[str, ...] = ()
        self.last_scan_scope = "project"

    @staticmethod
    def _claim_start(claim: ConsistencyClaim):
        return claim.valid_from_order if claim.valid_from_order is not None else claim.story_order

    @classmethod
    def _intervals_may_conflict(
        cls,
        source: ConsistencyClaim,
        candidate: ConsistencyClaim,
        *,
        predicate: str,
    ) -> bool:
        """Return false only when the active rule cannot produce a conflict.

        Unknown positions are deliberately retained. Ownership treats an earlier
        open-ended state as active until replaced; location does not infer that a
        character stayed somewhere forever, matching the rule implementations.
        """
        source_start = cls._claim_start(source)
        candidate_start = cls._claim_start(candidate)
        if source_start is None or candidate_start is None:
            return True
        if (
            source.valid_to_order is not None
            and source.valid_to_order < source_start
        ) or (
            candidate.valid_to_order is not None
            and candidate.valid_to_order < candidate_start
        ):
            # Malformed derived intervals are retained for diagnosis instead of
            # being silently optimized away.
            return True
        if source_start == candidate_start:
            return True
        earlier, later_start = (
            (source, candidate_start)
            if source_start < candidate_start
            else (candidate, source_start)
        )
        if predicate == "owns" and earlier.valid_to_order is None:
            return True
        return earlier.valid_to_order is not None and earlier.valid_to_order > later_start

    @classmethod
    def _prune_disjoint_temporal_claims(
        cls,
        claims: list[ConsistencyClaim],
        source_rows: list[ConsistencyClaim],
        *,
        chapter_id: str,
    ) -> list[ConsistencyClaim]:
        """Prune only disjoint ownership/location rows tied to a changed interval.

        ``owns`` has an open-ended active interval, while ``located_at`` does not
        infer that a character remains in one place after a later narration point.
        The latter deliberately follows ``_check_location_conflicts``: only an
        equal start or an explicit overlapping end can be a conflict. All other
        rule families (life, knowledge, ability and uncertain time) are retained.
        """
        result: list[ConsistencyClaim] = []
        for candidate in claims:
            if candidate.chapter_id == chapter_id or candidate.predicate not in {"owns", "located_at"}:
                result.append(candidate)
                continue

            if candidate.predicate == "owns":
                candidate_key = candidate.object_entry_id
                identity_sources = [
                    source
                    for source in source_rows
                    if source.predicate == "owns"
                    and candidate_key
                    and source.object_entry_id == candidate_key
                ]
            else:
                candidate_key = cls._subject_key(candidate)
                identity_sources = [
                    source
                    for source in source_rows
                    if source.predicate == "located_at"
                    and cls._subject_key(source) == candidate_key
                ]
            relevant = [
                source
                for source in identity_sources
                if source.timeline_id == candidate.timeline_id
            ]

            # A broad entity key may have loaded this row for another predicate;
            # without any same-rule source identity, retaining it is safest. A
            # matching identity on another timeline is provably isolated and is
            # therefore omitted (``relevant`` is empty).
            if not identity_sources or any(
                cls._intervals_may_conflict(source, candidate, predicate=candidate.predicate)
                for source in relevant
            ):
                result.append(candidate)
        return result

    @classmethod
    def _interval_possible_clause(
        cls,
        source: ConsistencyClaim,
    ):
        """SQL equivalent of the safe part of ``_intervals_may_conflict``.

        This deliberately returns a superset for unknown or malformed bounds;
        the in-memory pass remains authoritative after the query.
        """
        source_start = cls._claim_start(source)
        candidate_start = func.coalesce(
            ConsistencyClaim.valid_from_order,
            ConsistencyClaim.story_order,
        )
        if source_start is None:
            return True
        if source.valid_to_order is not None and source.valid_to_order < source_start:
            return True

        candidate_malformed = and_(
            candidate_start.is_not(None),
            ConsistencyClaim.valid_to_order.is_not(None),
            ConsistencyClaim.valid_to_order < candidate_start,
        )
        conditions = [
            candidate_start.is_(None),
            candidate_malformed,
            candidate_start == source_start,
            and_(
                candidate_start < source_start,
                or_(
                    ConsistencyClaim.valid_to_order.is_(None),
                    ConsistencyClaim.valid_to_order > source_start,
                ),
            ),
        ]
        if source.valid_to_order is None:
            conditions.append(candidate_start > source_start)
        else:
            conditions.append(
                and_(
                    candidate_start > source_start,
                    candidate_start < source.valid_to_order,
                )
            )
        return or_(*conditions)

    @staticmethod
    def _same_timeline_clause(timeline_id: str | None):
        return (
            ConsistencyClaim.timeline_id.is_(None)
            if timeline_id is None
            else ConsistencyClaim.timeline_id == timeline_id
        )

    @classmethod
    def _owns_interval_pushdown_clause(
        cls,
        source_rows: list[ConsistencyClaim],
        *,
        chapter_id: str,
    ):
        """Build a conservative SQL pre-filter for ownership intervals.

        A candidate with no matching source object id is retained. Candidates on
        another timeline are intentionally excluded here, matching the existing
        in-memory identity isolation; changed-chapter claims always bypass the
        pre-filter and are checked normally.
        """
        sources = [
            source
            for source in source_rows
            if source.predicate == "owns" and source.object_entry_id
        ]
        # Ownership identity intentionally has no text fallback: the in-memory
        # rule only compares resolved object_entry_id values, so unresolved
        # sources and candidates are retained by the broad predicate branches.
        if not sources:
            return None
        object_ids = sorted({source.object_entry_id for source in sources})
        possible_matches = [
            and_(
                ConsistencyClaim.object_entry_id == source.object_entry_id,
                cls._same_timeline_clause(source.timeline_id),
                cls._interval_possible_clause(source),
            )
            for source in sources
        ]
        return or_(
            ConsistencyClaim.chapter_id == chapter_id,
            ConsistencyClaim.predicate != "owns",
            ConsistencyClaim.object_entry_id.is_(None),
            ~ConsistencyClaim.object_entry_id.in_(object_ids),
            or_(*possible_matches),
        )

    @classmethod
    def _located_at_interval_pushdown_clause(
        cls,
        source_rows: list[ConsistencyClaim],
        *,
        chapter_id: str,
    ):
        """Build a conservative SQL pre-filter for resolved location intervals.

        Location identity follows the in-memory rule's subject key.  Only claims
        with a resolved subject entry are pushed down; text-only subjects remain
        in the broad impact set so normalization or future linking changes cannot
        turn a safe optimization into a missed conflict.
        """
        sources = [
            source
            for source in source_rows
            if source.predicate == "located_at" and source.subject_entry_id
        ]
        # A text-only source may later be linked to the same entity as a
        # resolved source.  Keep the whole location family in memory in that
        # mixed case; the optimization must not depend on today's fallback key.
        if not sources or any(
            source.predicate == "located_at" and not source.subject_entry_id
            for source in source_rows
        ):
            return None
        subject_ids = sorted({source.subject_entry_id for source in sources})
        possible_matches = [
            and_(
                ConsistencyClaim.subject_entry_id == source.subject_entry_id,
                cls._same_timeline_clause(source.timeline_id),
                cls._interval_possible_clause(source),
            )
            for source in sources
        ]
        return or_(
            ConsistencyClaim.chapter_id == chapter_id,
            ConsistencyClaim.predicate != "located_at",
            ConsistencyClaim.subject_entry_id.is_(None),
            ~ConsistencyClaim.subject_entry_id.in_(subject_ids),
            or_(*possible_matches),
        )

    async def _load_impacted_claims(
        self,
        db: AsyncSession,
        *,
        project_id: str,
        chapter_id: str,
    ) -> list[ConsistencyClaim]:
        """Load the changed chapter's entity/predicate closure, with safe fallback."""
        source_rows = list(
            (
                await db.execute(
                    select(ConsistencyClaim).where(
                        ConsistencyClaim.project_id == project_id,
                        ConsistencyClaim.chapter_id == chapter_id,
                        ConsistencyClaim.status.in_(["accepted", "superseded"]),
                    )
                )
            )
            .scalars()
            .all()
        )

        entity_ids = {
            entry_id
            for claim in source_rows
            for entry_id in (claim.subject_entry_id, claim.object_entry_id)
            if entry_id
        }
        unresolved: dict[str, set[str]] = {}
        temporal_event_refs: set[str] = set()
        for claim in source_rows:
            if claim.temporal_event_ref:
                temporal_event_refs.add(claim.temporal_event_ref.strip().casefold())
            if claim.subject_entry_id:
                continue
            subject = claim.subject_text.strip().casefold()
            if not subject:
                continue
            predicates = IMPACT_PREDICATE_FAMILIES.get(
                claim.predicate,
                frozenset({claim.predicate}),
            )
            for predicate in predicates:
                unresolved.setdefault(predicate, set()).add(subject)

        key_count = (
            len(entity_ids)
            + len(temporal_event_refs)
            + sum(len(values) for values in unresolved.values())
        )

        # Follow temporal references transitively. If chapter A changes event X,
        # a claim for Y relative to X and a claim for Z relative to Y must both be
        # rescanned after reflow. Loading only rows whose temporal_event_ref is X
        # silently misses the downstream chain.
        temporal_dependency_ids: set[int] = set()
        if temporal_event_refs:
            temporal_rows = (
                await db.execute(
                    select(
                        ConsistencyClaim.id,
                        ConsistencyClaim.temporal_event_ref,
                        ConsistencyClaim.temporal_relation_ref,
                    ).where(
                        ConsistencyClaim.project_id == project_id,
                        ConsistencyClaim.status == "accepted",
                    )
                )
            ).all()
            frontier = set(temporal_event_refs)
            visited_refs: set[str] = set()
            while frontier:
                current = frontier - visited_refs
                if not current:
                    break
                visited_refs.update(current)
                next_refs: set[str] = set()
                for row in temporal_rows:
                    event_ref = (row.temporal_event_ref or "").strip().casefold()
                    relation_ref = (row.temporal_relation_ref or "").strip().casefold()
                    if event_ref in current or relation_ref in current:
                        temporal_dependency_ids.add(row.id)
                    if relation_ref in current and event_ref and event_ref not in visited_refs:
                        next_refs.add(event_ref)
                frontier = next_refs
            key_count += len(temporal_dependency_ids)
        if not source_rows or key_count == 0 or key_count > MAX_IMPACT_KEYS:
            self.last_scan_scope = "project"
            self.last_scan_pruned_count = 0
            self.last_scan_interval_filter_applied = False
            self.last_scan_interval_filter_families = ()
            query = select(ConsistencyClaim).where(
                ConsistencyClaim.project_id == project_id,
                ConsistencyClaim.status == "accepted",
            )
        else:
            clauses = [ConsistencyClaim.chapter_id == chapter_id]
            if entity_ids:
                clauses.extend(
                    [
                        ConsistencyClaim.subject_entry_id.in_(entity_ids),
                        ConsistencyClaim.object_entry_id.in_(entity_ids),
                    ]
                )
            for predicate, subjects in unresolved.items():
                clauses.append(
                    and_(
                        ConsistencyClaim.predicate == predicate,
                        func.lower(func.trim(ConsistencyClaim.subject_text)).in_(subjects),
                    )
                )
            if temporal_event_refs:
                clauses.append(
                    func.lower(func.trim(ConsistencyClaim.temporal_event_ref)).in_(
                        temporal_event_refs
                    )
                )
            if temporal_dependency_ids:
                clauses.append(ConsistencyClaim.id.in_(temporal_dependency_ids))
            self.last_scan_scope = "impact"
            query = select(ConsistencyClaim).where(
                ConsistencyClaim.project_id == project_id,
                ConsistencyClaim.status == "accepted",
                or_(*clauses),
            )
            owns_pushdown = self._owns_interval_pushdown_clause(
                source_rows,
                chapter_id=chapter_id,
            )
            located_pushdown = self._located_at_interval_pushdown_clause(
                source_rows,
                chapter_id=chapter_id,
            )
            pushdown_clauses = [
                clause for clause in (owns_pushdown, located_pushdown) if clause is not None
            ]
            self.last_scan_interval_filter_families = tuple(
                family
                for family, clause in (
                    ("owns", owns_pushdown),
                    ("located_at", located_pushdown),
                )
                if clause is not None
            )
            if pushdown_clauses:
                # Each predicate family gets its own conservative filter.  The
                # other family is explicitly retained by that filter, so ANDing
                # them cannot remove unrelated claims.
                query = query.where(and_(*pushdown_clauses))
                self.last_scan_interval_filter_applied = True
            else:
                self.last_scan_interval_filter_applied = False

        claims = list((await db.execute(query.order_by(ConsistencyClaim.id))).scalars().all())
        if self.last_scan_scope == "impact":
            pruned = self._prune_disjoint_temporal_claims(
                claims,
                source_rows,
                chapter_id=chapter_id,
            )
            self.last_scan_pruned_count = len(claims) - len(pruned)
            return pruned
        return claims

    def compute_issue_fingerprint(self, issue_type: str, identity_keys: list[str]) -> str:
        """按语义身份计算稳定指纹。

        identity_keys 必须是跨 body_rev 稳定的语义键（实体 id / 主体文本 /
        时间线 / 对象值），不能包含 claim 行 id 或 run id。
        """
        canonical = json.dumps(
            {"type": issue_type, "keys": sorted(identity_keys)},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _merge_by_fingerprint(self, detected_issues: list[dict]) -> list[tuple[str, dict]]:
        """把同指纹的多次命中合并成一条 issue，顺序确定。

        必须合并，否则同一次扫描内就无法幂等：反复「死—活—死—活」会产生两对证据
        claim，两者指纹相同 —— 逐条 upsert 会让同一行先写第一对、再被第二对覆盖，
        下一次扫描又反向覆盖一遍，issue_rev 每扫一次涨两级，作者的处置永远对不上
        当前 rev。

        合并规则：保留**第一次**命中的锚点/描述/严重级别（claim 已按
        (story_order, id) 排序，第一次命中就是故事里最早出问题的地方，也是作者该
        先看的地方），证据 claim 取各次命中的有序并集 —— 后续几次的证据不丢。
        """
        merged: dict[str, dict] = {}
        ordered_fingerprints: list[str] = []

        for issue_data in detected_issues:
            fingerprint = self.compute_issue_fingerprint(
                issue_data["issue_type"], issue_data["identity_keys"]
            )
            if fingerprint not in merged:
                merged[fingerprint] = {
                    **issue_data,
                    "evidence_claims": list(issue_data["evidence_claims"]),
                }
                ordered_fingerprints.append(fingerprint)
                continue

            claim_ids = merged[fingerprint]["evidence_claims"]
            for claim_id in issue_data["evidence_claims"]:
                if claim_id not in claim_ids:
                    claim_ids.append(claim_id)

        return [(fingerprint, merged[fingerprint]) for fingerprint in ordered_fingerprints]

    async def scan_chapter(
        self,
        db: AsyncSession,
        *,
        run_id: int,
        project_id: str,
        chapter_id: str,
        body_rev: int,
    ) -> list[str]:
        """
        Scan a chapter's claims and produce/update GuardIssue records
        Cross-chapter consistency: load all project claims for timeline analysis

        同一版正文重复扫描是完全幂等的：实质证据没变的 issue 一个字段都不动
        （不增 issue_rev、不改 run_id、不删写 evidence 行），见 _upsert_issue。

        Returns:
            List of issue IDs (new or updated)
        """
        all_claims = await self._load_impacted_claims(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
        )
        self.last_scan_claim_count = len(all_claims)
        claims_by_id = {claim.id: claim for claim in all_claims}

        # Run rules on all project claims
        detected_issues = []
        detected_issues.extend(await self._check_alive_conflicts(db, project_id, all_claims))
        detected_issues.extend(await self._check_ownership_conflicts(db, project_id, all_claims))
        detected_issues.extend(await self._check_knowledge_boundary(db, project_id, all_claims))
        detected_issues.extend(await self._check_timeline_conflicts(db, project_id, all_claims))
        detected_issues.extend(await self._check_ability_boundary(db, project_id, all_claims))
        detected_issues.extend(await self._check_location_conflicts(db, project_id, all_claims))
        detected_issues.extend(
            await self._check_foreshadow_overdue(db, project_id, chapter_id)
        )

        # Upsert issues（同指纹先合并，一个指纹只写一次）
        issue_ids = []
        for fingerprint, issue_data in self._merge_by_fingerprint(detected_issues):
            issue_id = await self._upsert_issue(
                db,
                run_id=run_id,
                project_id=project_id,
                chapter_id=chapter_id,
                fingerprint=fingerprint,
                issue_data=issue_data,
                claims_by_id=claims_by_id,
            )
            issue_ids.append(issue_id)

        # Mark stale issues
        await self._mark_stale_issues(db, run_id, project_id, chapter_id, issue_ids)

        return issue_ids

    @staticmethod
    def _subject_key(claim: ConsistencyClaim) -> str:
        """主体的稳定身份：优先已解析的 entry_id，否则退回文本。"""
        return claim.subject_entry_id or f"text:{claim.subject_text.strip().lower()}"

    @staticmethod
    def _object_key(claim: ConsistencyClaim) -> str:
        """对象的稳定身份：优先条目 id，否则使用规范化文本。

        调用方必须先过滤 object_entry_id 和 object_value 均为空的 claim。
        """
        return claim.object_entry_id or f"text:{(claim.object_value or '').strip().casefold()}"

    @staticmethod
    def _capped_severity(severity: str, claims: list[ConsistencyClaim]) -> str:
        """按证据 claim 的确定性给严重级别设上限。

        架构 7：「任一输入 claim 为 uncertain 时不得产生 high severity。」
        uncertain 意味着正文本身没把这件事说定，据此出 high 会把作者的模糊表达
        当成硬矛盾。降到 medium：仍然提示，但不占用「必须处理」的注意力。
        """
        if severity == "high" and any(c.certainty == "uncertain" for c in claims):
            return "medium"
        return severity

    async def _check_alive_conflicts(
        self,
        db: AsyncSession,
        project_id: str,
        claims: list[ConsistencyClaim],
    ) -> list[dict]:
        """Check for death followed by alive=true (resurrection without explanation)"""
        issues = []
        alive_claims = [c for c in claims if c.predicate == "alive" and c.object_type == "scalar"]

        # Group by subject and timeline
        by_subject_timeline: dict[tuple[str, Optional[str]], list[ConsistencyClaim]] = {}
        for claim in alive_claims:
            key = (self._subject_key(claim), claim.timeline_id)
            by_subject_timeline.setdefault(key, []).append(claim)

        for (subject_key, timeline_id), group in by_subject_timeline.items():
            ordered = [c for c in group if c.story_order is not None]
            # story_order 缺失时无法判断先后，跳过（宁可漏报也不误报）
            if len(ordered) < 2:
                continue

            sorted_claims = sorted(ordered, key=lambda c: (c.story_order, c.id))

            # Check for death (alive=false) followed by alive=true
            for i in range(len(sorted_claims) - 1):
                current = sorted_claims[i]
                next_claim = sorted_claims[i + 1]

                # Death followed by alive?
                if (current.object_value == "false" and current.polarity == "positive" and
                        next_claim.object_value == "true" and next_claim.polarity == "positive"):
                    issues.append({
                        "issue_type": "alive_conflict",
                        "severity": self._capped_severity("high", [current, next_claim]),
                        "confidence": 0.95,
                        "description": (
                            f"{next_claim.subject_text} marked as dead then alive without explanation"
                        ),
                        "evidence_claims": [current.id, next_claim.id],
                        # 锚点指向「后来那条」—— 复活是新出现的问题所在
                        "anchor": claim_anchor(next_claim),
                        "identity_keys": [
                            subject_key,
                            f"timeline:{timeline_id}",
                            "alive:false->true",
                        ],
                    })

        return issues

    async def _check_ownership_conflicts(
        self,
        db: AsyncSession,
        project_id: str,
        claims: list[ConsistencyClaim],
    ) -> list[dict]:
        """Check for ownership conflicts (same item, different owners)"""
        issues = []
        ownership_claims = [c for c in claims if c.predicate == "owns" and c.object_type == "entity"]

        # Group by object (the item being owned) and timeline
        by_object_timeline: dict[tuple[str, Optional[str]], list[ConsistencyClaim]] = {}
        for claim in ownership_claims:
            if claim.object_entry_id:
                key = (claim.object_entry_id, claim.timeline_id)
                by_object_timeline.setdefault(key, []).append(claim)

        for (object_id, timeline_id), group in by_object_timeline.items():
            ordered = [c for c in group if c.story_order is not None]
            if len(ordered) < 2:
                continue

            sorted_claims = sorted(ordered, key=lambda c: (c.story_order, c.id))

            # Check for different owners at overlapping times
            for i in range(len(sorted_claims) - 1):
                current = sorted_claims[i]
                next_claim = sorted_claims[i + 1]

                if self._subject_key(current) == self._subject_key(next_claim):
                    continue

                # 区间重叠判定。到这里 story_order 一定非空（上面已过滤），而
                # assign_narrative_positions 只会给顺序可靠的状态型 claim 补
                # valid_from_order，所以兜底取 story_order 不会引入伪造的顺序 ——
                # 顺序不可靠的 claim 的 story_order 本身就是 NULL，进不到这里。
                next_from = (
                    next_claim.valid_from_order
                    if next_claim.valid_from_order is not None
                    else next_claim.story_order
                )
                if current.valid_to_order is None or current.valid_to_order > next_from:
                    issues.append({
                        "issue_type": "ownership_conflict",
                        "severity": self._capped_severity("high", [current, next_claim]),
                        "confidence": 0.90,
                        "description": "Item owned by different entities at overlapping times",
                        "evidence_claims": [current.id, next_claim.id],
                        # 锚点指向后出现的那条归属声明
                        "anchor": claim_anchor(next_claim),
                        "identity_keys": [
                            f"item:{object_id}",
                            f"timeline:{timeline_id}",
                            f"owners:{'|'.join(sorted([self._subject_key(current), self._subject_key(next_claim)]))}",
                        ],
                    })

        return issues

    async def _check_knowledge_boundary(
        self,
        db: AsyncSession,
        project_id: str,
        claims: list[ConsistencyClaim],
    ) -> list[dict]:
        """Check for knowledge used before its narrative introduction"""
        issues = []

        # Look for "uses_knowledge" and "acquires_knowledge" predicates
        knowledge_uses = [c for c in claims if c.predicate == "uses_knowledge"]
        knowledge_acquires = [c for c in claims if c.predicate == "acquires_knowledge"]

        for use_claim in knowledge_uses:
            if use_claim.story_order is None:
                continue

            for acquire_claim in knowledge_acquires:
                if acquire_claim.story_order is None:
                    continue

                # Same subject, same knowledge object, AND same timeline?
                # 不同 timeline 的知情状态互不影响；timeline 为空或不同不比较。
                if (self._subject_key(use_claim) == self._subject_key(acquire_claim) and
                        use_claim.object_value == acquire_claim.object_value and
                        use_claim.timeline_id is not None and
                        use_claim.timeline_id == acquire_claim.timeline_id):

                    # Used before acquired?
                    if use_claim.story_order < acquire_claim.story_order:
                        issues.append({
                            "issue_type": "knowledge_boundary",
                            "severity": "medium",
                            "confidence": 0.85,
                            "description": (
                                f"{use_claim.subject_text} uses knowledge "
                                f"'{use_claim.object_value}' before acquiring it"
                            ),
                            "evidence_claims": [use_claim.id, acquire_claim.id],
                            # 锚点指向「提前使用」那条 —— 需要作者改的是这里
                            "anchor": claim_anchor(use_claim),
                            "identity_keys": [
                                self._subject_key(use_claim),
                                f"knowledge:{use_claim.object_value}",
                                f"timeline:{use_claim.timeline_id}",
                            ],
                        })

        return issues

    async def _check_timeline_conflicts(
        self,
        db: AsyncSession,
        project_id: str,
        claims: list[ConsistencyClaim],
    ) -> list[dict]:
        """同一时间线中的同一具名事件不能落在两个可靠绝对时刻。"""
        del db, project_id
        groups: dict[tuple[str, str], list[ConsistencyClaim]] = {}
        for claim in claims:
            event_ref = (claim.temporal_event_ref or "").strip().casefold()
            if not event_ref or not claim.timeline_id or claim.story_order is None:
                continue
            groups.setdefault((claim.timeline_id, event_ref), []).append(claim)

        issues = []
        for (timeline_id, event_ref), group in groups.items():
            ordered = sorted(group, key=lambda c: (c.story_order, c.id))
            baseline = ordered[0]
            conflicting = [claim for claim in ordered[1:] if claim.story_order != baseline.story_order]
            if not conflicting:
                continue
            offender = conflicting[0]
            evidence = [baseline, *conflicting]
            issues.append({
                "issue_type": "timeline_conflict",
                "severity": self._capped_severity("high", evidence),
                "confidence": 0.95,
                "description": f"事件“{baseline.temporal_event_ref}”被标注为不同时间",
                "evidence_claims": [claim.id for claim in evidence],
                "anchor": claim_anchor(offender),
                "identity_keys": [f"timeline:{timeline_id}", f"event:{event_ref}"],
            })
        return issues

    async def _check_ability_boundary(
        self,
        db: AsyncSession,
        project_id: str,
        claims: list[ConsistencyClaim],
    ) -> list[dict]:
        """角色在同一时间线中先使用、后获得同一能力时报警。"""
        del db, project_id
        uses = [
            claim
            for claim in claims
            if claim.predicate == "uses_ability"
            and (claim.object_entry_id or (claim.object_value or "").strip())
        ]
        acquires = [
            claim
            for claim in claims
            if claim.predicate == "acquires_ability"
            and (claim.object_entry_id or (claim.object_value or "").strip())
        ]
        issues = []
        for use_claim in uses:
            if use_claim.story_order is None or not use_claim.timeline_id:
                continue
            matching = [
                claim
                for claim in acquires
                if claim.story_order is not None
                and claim.timeline_id == use_claim.timeline_id
                and self._subject_key(claim) == self._subject_key(use_claim)
                and self._object_key(claim) == self._object_key(use_claim)
                and use_claim.story_order < claim.story_order
            ]
            if not matching:
                continue
            acquired = min(matching, key=lambda c: (c.story_order, c.id))
            evidence = [use_claim, acquired]
            issues.append({
                "issue_type": "ability_boundary",
                "severity": self._capped_severity("high", evidence),
                "confidence": 0.90,
                "description": (
                    f"{use_claim.subject_text}在获得“{use_claim.object_value}”前使用了该能力"
                ),
                "evidence_claims": [use_claim.id, acquired.id],
                "anchor": claim_anchor(use_claim),
                "identity_keys": [
                    self._subject_key(use_claim),
                    f"ability:{self._object_key(use_claim)}",
                    f"timeline:{use_claim.timeline_id}",
                ],
            })
        return issues

    async def _check_location_conflicts(
        self,
        db: AsyncSession,
        project_id: str,
        claims: list[ConsistencyClaim],
    ) -> list[dict]:
        """只检测同一可靠时刻或明确重叠区间内的双重地点。"""
        del db, project_id
        groups: dict[tuple[str, str], list[ConsistencyClaim]] = {}
        for claim in claims:
            if (
                claim.predicate != "located_at"
                or not claim.timeline_id
                or claim.story_order is None
                or not (claim.object_entry_id or (claim.object_value or "").strip())
            ):
                continue
            groups.setdefault((self._subject_key(claim), claim.timeline_id), []).append(claim)

        issues = []
        for (subject_key, timeline_id), group in groups.items():
            ordered = sorted(group, key=lambda c: (c.story_order, c.id))
            for index, current in enumerate(ordered):
                current_start = current.valid_from_order or current.story_order
                for candidate in ordered[index + 1:]:
                    if self._object_key(current) == self._object_key(candidate):
                        continue
                    candidate_start = candidate.valid_from_order or candidate.story_order
                    # An open-ended earlier location is not proof that the character
                    # stayed there forever. Ordinary travel commonly produces exactly
                    # that shape across separate extraction batches. Outside the same
                    # instant, require an explicit earlier interval end that overlaps
                    # the later location before raising a hard conflict. Intervals
                    # are half-open [from, to), so adjacent intervals do not overlap.
                    overlaps = current_start == candidate_start or (
                        current.valid_to_order is not None
                        and current.valid_to_order > candidate_start
                    )
                    if not overlaps:
                        continue
                    evidence = [current, candidate]
                    issues.append({
                        "issue_type": "location_conflict",
                        "severity": self._capped_severity("high", evidence),
                        "confidence": 0.92,
                        "description": (
                            f"{candidate.subject_text}在同一时段同时位于“"
                            f"{current.object_value}”和“{candidate.object_value}”"
                        ),
                        "evidence_claims": [current.id, candidate.id],
                        "anchor": claim_anchor(candidate),
                        "identity_keys": [
                            subject_key,
                            f"timeline:{timeline_id}",
                            "locations:"
                            + "|".join(sorted([self._object_key(current), self._object_key(candidate)])),
                        ],
                    })
        return issues

    async def _check_foreshadow_overdue(
        self,
        db: AsyncSession,
        project_id: str,
        scanned_chapter_id: str,
    ) -> list[dict]:
        """在当前故事进度超过预计章节后，提示仍未回收的登记伏笔。"""
        current = (
            await db.execute(
                select(Chapter).where(
                    Chapter.id == scanned_chapter_id,
                    Chapter.project_id == project_id,
                    Chapter.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if current is None:
            logger.warning(
                "Foreshadow overdue check skipped: active chapter %s not found in project %s",
                scanned_chapter_id,
                project_id,
            )
            return []

        rows = list(
            (
                await db.execute(
                    select(Foreshadow, CodexEntry, Chapter)
                    .join(CodexEntry, CodexEntry.id == Foreshadow.entry_id)
                    .join(Chapter, Chapter.id == Foreshadow.expected_chapter_id)
                    .where(
                        Foreshadow.project_id == project_id,
                        Foreshadow.resolved.is_(False),
                        Foreshadow.expected_chapter_id.is_not(None),
                        CodexEntry.status == "confirmed",
                        Chapter.deleted_at.is_(None),
                        Chapter.idx < current.idx,
                    )
                    .order_by(Chapter.idx, Foreshadow.id)
                )
            ).all()
        )
        issues = []
        for lifecycle, entry, expected in rows:
            issues.append({
                "issue_type": "foreshadow_overdue",
                "severity": "medium",
                "confidence": 1.0,
                "description": f"伏笔“{entry.name}”已超过预计回收章节",
                "evidence_claims": [],
                "evidence_versions": [{
                    "kind": "foreshadow",
                    "entry_id": entry.id,
                    "chapter_id": expected.id,
                    "planted_chapter_id": lifecycle.planted_chapter_id,
                    "expected_chapter_id": expected.id,
                    "resolved": lifecycle.resolved,
                    "updated_at": lifecycle.updated_at.isoformat() if lifecycle.updated_at else None,
                }],
                "anchor": {
                    "chapter_id": expected.id,
                    "body_rev": None,
                    "pid": None,
                    "entry_id": entry.id,
                },
                "entry_id": entry.id,
                "identity_keys": [f"foreshadow:{entry.id}"],
            })
        return issues

    @staticmethod
    def _claim_versions(
        claim_ids: list[int],
        claims_by_id: dict[int, ConsistencyClaim],
    ) -> list[dict[str, Any]]:
        """证据 claim 的**版本身份**：决定 issue 是否真的换了证据。

        只有 claim id、来源章节/正文版本、来源锚点这些能证明「证据换了一版」的
        字段才进来。confidence / story_order 这类每次重算都可能微调的派生值不进来
        —— 否则一次无意义的重算就会把作者刚处置完的 issue 重新翻出来。
        """
        versions = []
        for claim_id in claim_ids:
            claim = claims_by_id.get(claim_id)
            if claim is None:
                versions.append({"claim_id": claim_id})
                continue
            versions.append(
                {
                    "claim_id": claim.id,
                    "chapter_id": claim.chapter_id,
                    "body_rev": claim.body_rev,
                    "outline_rev": claim.outline_rev,
                    "source_anchor": claim.source_anchor,
                }
            )
        return versions

    def _evidence_signature(
        self,
        claim_versions: list[dict[str, Any]],
        anchor: dict[str, Any],
    ) -> str:
        """实质证据的签名：claim 版本身份 + 锚点 + 规则版本。

        重复扫描的幂等性完全建立在这个签名上：签名相同就一个字段都不动。
        """
        canonical = json.dumps(
            {
                "claims": claim_versions,
                "anchor": anchor,
                "rule_version": self.rule_version,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    async def _select_issue(
        db: AsyncSession,
        project_id: str,
        fingerprint: str,
    ) -> Optional[GuardIssue]:
        result = await db.execute(
            select(GuardIssue)
            .where(GuardIssue.project_id == project_id)
            .where(GuardIssue.fingerprint == fingerprint)
        )
        return result.scalar_one_or_none()

    async def _upsert_issue(
        self,
        db: AsyncSession,
        *,
        run_id: int,
        project_id: str,
        chapter_id: str,
        fingerprint: str,
        issue_data: dict,
        claims_by_id: dict[int, ConsistencyClaim],
    ) -> str:
        """Create or update GuardIssue, write GuardIssueEvidence.

        生命周期规则（每一条都对应一个被拒收的行为）：

        * 实质证据签名不变 ⇒ **一个字段都不写**：不增 issue_rev、不改 run_id、
          不动 evidence 行。否则每次保存正文都会让全项目的 issue 集体涨 rev，
          作者手上的 issue_rev 立刻过期，处置接口一律 409。
        * 签名变化 ⇒ issue_rev+1 并刷新证据；未被误报判定的 issue 同时重开。
        * resolved：同一实质证据再扫描保持 resolved（那是作者对这份证据的判断）；
          证据换版才重开。
        * false_positive：永不复活；但证据换版时必须连同 issue_rev 一起更新，
          不能悄悄把新证据塞进作者判过的那个 rev 里。
        * stale：冲突重新出现就重开（stale 是系统观察，不是作者决定），
          issue_rev 仍然只在证据变化时才涨。
        """
        anchor = issue_data["anchor"]
        claim_ids = list(issue_data["evidence_claims"])
        claim_versions = issue_data.get(
            "evidence_versions", self._claim_versions(claim_ids, claims_by_id)
        )
        signature = self._evidence_signature(claim_versions, anchor)
        evidence_payload = {
            "claim_ids": claim_ids,
            "claim_versions": claim_versions,
            "rule_version": self.rule_version,
            "detected_in_chapter": anchor.get("chapter_id"),
            "detected_in_body_rev": anchor.get("body_rev"),
            "evidence_signature": signature,
        }
        actions = ISSUE_ACTIONS.get(issue_data["issue_type"], DEFAULT_ACTIONS)

        existing = await self._select_issue(db, project_id, fingerprint)

        if existing is None:
            issue_id = f"gi_{secrets.token_hex(12)}"
            issue = GuardIssue(
                id=issue_id,
                project_id=project_id,
                # 归属到出问题的那一章（可能不是被扫描的那一章）
                chapter_id=anchor.get("chapter_id") or chapter_id,
                run_id=run_id,
                entry_id=issue_data.get("entry_id"),
                issue_type=issue_data["issue_type"],
                rule_version=self.rule_version,
                fingerprint=fingerprint,
                severity=issue_data["severity"],
                confidence=issue_data["confidence"],
                description=issue_data["description"],
                evidence=evidence_payload,
                anchor=anchor,
                actions=actions,
                status="open",
                issue_rev=1,
                resolved=False,
                false_positive=False,
                arbitration_status="pending",
            )
            try:
                # uq_guard_issue_fingerprint(project_id, fingerprint) 上有并发窗口：
                # 两个 worker 同时扫同一个项目时，「先查后插」之间另一方可能已经插入。
                # SAVEPOINT 把撞键限制在这一条语句里，外层事务（本次扫描已经写好的
                # 其他 issue）不受影响；回滚后改走「已存在」分支。
                # 不用 PostgreSQL 的 ON CONFLICT：SQLite 上无法编译，这段逻辑就再也
                # 没有单元测试能覆盖。
                async with db.begin_nested():
                    db.add(issue)
                    await db.flush()
            except IntegrityError:
                existing = await self._select_issue(db, project_id, fingerprint)
                if existing is None:
                    raise
            else:
                await self._write_evidence(
                    db,
                    issue_id,
                    claim_ids,
                    claims_by_id,
                    codex_entry_id=issue_data.get("entry_id"),
                )
                return issue_id

        stored_signature = (existing.evidence or {}).get("evidence_signature")
        evidence_changed = stored_signature != signature
        is_false_positive = existing.false_positive or existing.status == "false_positive"
        # 误报不复活，所以误报的 issue 永远不进重开分支
        needs_reopen = existing.status == "stale" and not is_false_positive

        if not evidence_changed and not needs_reopen:
            # 完全幂等：不碰任何字段，连 updated_at 都不动
            return existing.id

        if evidence_changed:
            existing.chapter_id = anchor.get("chapter_id") or chapter_id
            existing.entry_id = issue_data.get("entry_id")
            existing.severity = issue_data["severity"]
            existing.confidence = issue_data["confidence"]
            existing.description = issue_data["description"]
            existing.anchor = anchor
            existing.evidence = evidence_payload
            existing.actions = actions
            existing.rule_version = self.rule_version
            existing.issue_rev += 1
            if not is_false_positive:
                existing.arbitration_status = "pending"
                existing.arbitration_confidence = None
                existing.arbitration_rationale = None
                existing.arbitration_model = None
                existing.arbitration_version = None
                existing.arbitration_error = None
                existing.arbitrated_at = None

        if not is_false_positive:
            existing.status = "open"
            existing.stale_at = None
            if evidence_changed:
                # 换了一版证据，作者上一次的处置不再适用于当前 rev
                existing.resolved = False
                existing.resolution = None

        # 只有真被本次扫描改动的 issue 才记到本次 run 上
        existing.run_id = run_id
        existing.updated_at = datetime.now(timezone.utc)

        if evidence_changed:
            await self._write_evidence(
                db,
                existing.id,
                claim_ids,
                claims_by_id,
                codex_entry_id=issue_data.get("entry_id"),
            )
        await db.flush()

        return existing.id

    @staticmethod
    def _evidence_row_values(
        claim: ConsistencyClaim,
        idx: int,
    ) -> dict[str, Any]:
        """一条证据行的全部内容字段（不含 issue_id / 主键）。"""
        return {
            "side": "expected" if idx == 0 else "actual",
            "source_kind": "claim",
            "claim_id": claim.id,
            "codex_entry_id": None,
            "chapter_id": claim.chapter_id,
            "body_rev": claim.body_rev,
            "outline_rev": claim.outline_rev,
            "paragraph_id": claim.paragraph_id,
            "quote": claim.subject_text,
            "sort_order": idx,
        }

    async def _write_evidence(
        self,
        db: AsyncSession,
        issue_id: str,
        claim_ids: list[int],
        claims_by_id: dict[int, ConsistencyClaim],
        *,
        codex_entry_id: str | None = None,
    ):
        """Write expected/actual GuardIssueEvidence records.

        逐行比对后只改真正不同的行，内容一致时一行都不动。之前的实现每次扫描都先
        DELETE 再 INSERT：GuardIssueEvidence.id 是自增主键，重写会让证据行 id 全部
        变化，前端拿着旧 id 请求就 404；而且删写发生在同一事务里，PostgreSQL 上等于
        白白产生死行。
        """
        existing_rows = list(
            (
                await db.execute(
                    select(GuardIssueEvidence)
                    .where(GuardIssueEvidence.issue_id == issue_id)
                    .order_by(GuardIssueEvidence.sort_order, GuardIssueEvidence.id)
                )
            )
            .scalars()
            .all()
        )

        # 缺失的 claim（已被删/不在 accepted 集合里）不写证据行，与旧行为一致
        desired = [
            self._evidence_row_values(claims_by_id[claim_id], idx)
            for idx, claim_id in enumerate(
                claim_id for claim_id in claim_ids if claim_id in claims_by_id
            )
        ]
        if not desired and codex_entry_id:
            entry = await db.get(CodexEntry, codex_entry_id)
            if entry is not None:
                desired = [{
                    "side": "expected",
                    "source_kind": "codex",
                    "claim_id": None,
                    "codex_entry_id": entry.id,
                    "chapter_id": None,
                    "body_rev": None,
                    "outline_rev": None,
                    "paragraph_id": None,
                    "quote": f"{entry.name}：{entry.description}" if entry.description else entry.name,
                    "sort_order": 0,
                }]

        dirty = False
        for row, values in zip(existing_rows, desired):
            for field, value in values.items():
                if getattr(row, field) != value:
                    setattr(row, field, value)
                    dirty = True

        # 证据变多：补插；变少：删掉多出来的行
        for values in desired[len(existing_rows):]:
            db.add(GuardIssueEvidence(issue_id=issue_id, **values))
            dirty = True
        for row in existing_rows[len(desired):]:
            await db.delete(row)
            dirty = True

        if dirty:
            await db.flush()

    @staticmethod
    def _touches_chapter(issue: GuardIssue, chapter_id: str) -> bool:
        """本次扫描是否**有能力**重新发现这条 issue。

        判据是这条 issue 的证据里是否引用了被扫描的章节：锚点章节（issue.chapter_id）
        或任何一条证据 claim 的来源章节。跨章冲突的锚点落在后出现的那一章，作者却
        可能是改前一章把冲突解决的 —— 只按锚点章节判会让这条 issue 永远停在 open。

        反过来，与被扫描章节完全无关的 issue 不进 stale 判定：它的证据这次根本没被
        重新评估过（作者没有触发那一章的扫描），凭「这次没检出」把它标成 stale 就是
        在替作者做没有依据的判断。
        """
        # 伏笔到期状态是项目进度函数，每次章节扫描都完整重算；回收后必须能
        # 在任意后续扫描中变 stale，不能只等待再次编辑预计回收章。
        if issue.issue_type == "foreshadow_overdue":
            return True
        if issue.chapter_id == chapter_id:
            return True
        evidence = issue.evidence or {}
        return any(
            version.get("chapter_id") == chapter_id
            for version in evidence.get("claim_versions") or []
        )

    async def _mark_stale_issues(
        self,
        db: AsyncSession,
        run_id: int,
        project_id: str,
        chapter_id: str,
        current_issue_ids: list[str],
    ):
        """Mark previously open issues as stale if not rediscovered.

        只动真正被标成 stale 的那几行；已经不在本次检出结果里、但也与本章无关的
        issue 一个字段都不碰（不写 run_id、不改 updated_at）。
        """
        result = await db.execute(
            select(GuardIssue)
            .where(GuardIssue.project_id == project_id)
            .where(GuardIssue.status == "open")
        )
        all_open = list(result.scalars().all())

        dirty = False
        for issue in all_open:
            if issue.id in current_issue_ids:
                continue
            if not self._touches_chapter(issue, chapter_id):
                continue
            issue.status = "stale"
            issue.stale_at = datetime.now(timezone.utc)
            issue.updated_at = datetime.now(timezone.utc)
            dirty = True

        if dirty:
            await db.flush()
