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
"""
import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_consistency_extended import ConsistencyClaim, GuardIssueEvidence
from db.models_guard import GuardIssue

#: 每类冲突可供作者选择的处置动作，取值必须落在 GuardResolution 的 CHECK 约束内。
ISSUE_ACTIONS: dict[str, list[str]] = {
    "alive_conflict": ["accept_old_fact", "accept_new_fact", "intentional_exception", "false_positive"],
    "ownership_conflict": ["accept_old_fact", "accept_new_fact", "intentional_exception", "false_positive"],
    "knowledge_boundary": ["accept_new_fact", "intentional_exception", "fixed_in_body", "false_positive"],
}
DEFAULT_ACTIONS = ["accept_old_fact", "accept_new_fact", "intentional_exception", "false_positive"]


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

        Returns:
            List of issue IDs (new or updated)
        """
        # Load all accepted claims for the project (cross-chapter consistency)
        result = await db.execute(
            select(ConsistencyClaim)
            .where(ConsistencyClaim.project_id == project_id)
            .where(ConsistencyClaim.status == "accepted")
            .order_by(ConsistencyClaim.id)
        )
        all_claims = list(result.scalars().all())

        # Run rules on all project claims
        detected_issues = []
        detected_issues.extend(await self._check_alive_conflicts(db, project_id, all_claims))
        detected_issues.extend(await self._check_ownership_conflicts(db, project_id, all_claims))
        detected_issues.extend(await self._check_knowledge_boundary(db, project_id, all_claims))

        # Upsert issues
        issue_ids = []
        for issue_data in detected_issues:
            issue_id = await self._upsert_issue(db, run_id, project_id, chapter_id, issue_data)
            if issue_id not in issue_ids:
                issue_ids.append(issue_id)

        # Mark stale issues
        await self._mark_stale_issues(db, run_id, project_id, chapter_id, issue_ids)

        return issue_ids

    @staticmethod
    def _subject_key(claim: ConsistencyClaim) -> str:
        """主体的稳定身份：优先已解析的 entry_id，否则退回文本。"""
        return claim.subject_entry_id or f"text:{claim.subject_text.strip().lower()}"

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
                        "severity": "high",
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

                # 区间重叠判定；valid_from_order 缺失时用 story_order 兜底，
                # 否则 None 参与比较会直接抛 TypeError。
                next_from = (
                    next_claim.valid_from_order
                    if next_claim.valid_from_order is not None
                    else next_claim.story_order
                )
                if current.valid_to_order is None or current.valid_to_order > next_from:
                    issues.append({
                        "issue_type": "ownership_conflict",
                        "severity": "high",
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

                # Same subject and same knowledge object?
                if (self._subject_key(use_claim) == self._subject_key(acquire_claim) and
                        use_claim.object_value == acquire_claim.object_value):

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

    async def _upsert_issue(
        self,
        db: AsyncSession,
        run_id: int,
        project_id: str,
        chapter_id: str,
        issue_data: dict,
    ) -> str:
        """Create or update GuardIssue, write GuardIssueEvidence"""
        fingerprint = self.compute_issue_fingerprint(
            issue_data["issue_type"], issue_data["identity_keys"]
        )
        anchor = issue_data["anchor"]
        evidence_payload = {
            "claim_ids": list(issue_data["evidence_claims"]),
            "rule_version": self.rule_version,
            "detected_in_chapter": anchor.get("chapter_id"),
            "detected_in_body_rev": anchor.get("body_rev"),
        }
        actions = ISSUE_ACTIONS.get(issue_data["issue_type"], DEFAULT_ACTIONS)

        # Check if issue exists
        result = await db.execute(
            select(GuardIssue)
            .where(GuardIssue.project_id == project_id)
            .where(GuardIssue.fingerprint == fingerprint)
        )
        existing = result.scalar_one_or_none()

        if existing:
            issue_id = existing.id
            # 作者已判定为误报的 issue 不复活，只刷新证据与锚点
            if existing.false_positive or existing.status == "false_positive":
                existing.anchor = anchor
                existing.evidence = evidence_payload
                existing.updated_at = datetime.now(timezone.utc)
            else:
                existing.run_id = run_id
                existing.chapter_id = anchor.get("chapter_id") or chapter_id
                existing.status = "open"
                existing.resolved = False
                existing.stale_at = None
                existing.severity = issue_data["severity"]
                existing.confidence = issue_data["confidence"]
                existing.description = issue_data["description"]
                existing.anchor = anchor
                existing.evidence = evidence_payload
                existing.actions = actions
                existing.rule_version = self.rule_version
                existing.issue_rev += 1
                existing.updated_at = datetime.now(timezone.utc)
        else:
            issue_id = f"gi_{secrets.token_hex(12)}"
            issue = GuardIssue(
                id=issue_id,
                project_id=project_id,
                # 归属到出问题的那一章（可能不是被扫描的那一章）
                chapter_id=anchor.get("chapter_id") or chapter_id,
                run_id=run_id,
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
            )
            db.add(issue)
            await db.flush()

        # Write evidence records
        await self._write_evidence(db, issue_id, issue_data["evidence_claims"])

        return issue_id

    async def _write_evidence(
        self,
        db: AsyncSession,
        issue_id: str,
        claim_ids: list[int],
    ):
        """Write expected/actual GuardIssueEvidence records"""
        # Delete old evidence for this issue
        old_evidence = await db.execute(
            select(GuardIssueEvidence).where(GuardIssueEvidence.issue_id == issue_id)
        )
        for ev in old_evidence.scalars():
            await db.delete(ev)
        await db.flush()

        # Write new evidence
        for idx, claim_id in enumerate(claim_ids):
            claim_result = await db.execute(
                select(ConsistencyClaim).where(ConsistencyClaim.id == claim_id)
            )
            claim = claim_result.scalar_one_or_none()
            if not claim:
                continue

            side = "expected" if idx == 0 else "actual"
            evidence = GuardIssueEvidence(
                issue_id=issue_id,
                side=side,
                source_kind="claim",
                claim_id=claim.id,
                chapter_id=claim.chapter_id,
                body_rev=claim.body_rev,
                outline_rev=claim.outline_rev,
                paragraph_id=claim.paragraph_id,
                quote=claim.subject_text,
                sort_order=idx,
            )
            db.add(evidence)
        await db.flush()

    async def _mark_stale_issues(
        self,
        db: AsyncSession,
        run_id: int,
        project_id: str,
        chapter_id: str,
        current_issue_ids: list[str],
    ):
        """Mark previously open issues as stale if not rediscovered"""
        result = await db.execute(
            select(GuardIssue)
            .where(GuardIssue.project_id == project_id)
            .where(GuardIssue.chapter_id == chapter_id)
            .where(GuardIssue.status == "open")
        )
        all_open = list(result.scalars().all())

        for issue in all_open:
            if issue.id not in current_issue_ids:
                issue.status = "stale"
                issue.stale_at = datetime.now(timezone.utc)
                issue.updated_at = datetime.now(timezone.utc)
        await db.flush()
