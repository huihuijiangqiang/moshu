"""
Rule Scanner - Persistent consistency checking with fingerprinted issues
"""
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_consistency_extended import ConsistencyClaim
from db.models_guard import GuardIssue
from db.models_guard import GuardIssueEvidence


class RuleScanner:
    """Scans accepted claims and produces fingerprinted GuardIssue records"""

    def __init__(self, rule_version: str = "v1.0"):
        self.rule_version = rule_version

    def compute_issue_fingerprint(self, issue_type: str, evidence_keys: list[str]) -> str:
        """Compute stable fingerprint for an issue"""
        canonical = json.dumps({"type": issue_type, "keys": sorted(evidence_keys)}, sort_keys=True)
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

        Returns:
            List of issue IDs (new or updated)
        """
        # Load accepted claims for this chapter and body_rev
        result = await db.execute(
            select(ConsistencyClaim)
            .where(ConsistencyClaim.project_id == project_id)
            .where(ConsistencyClaim.chapter_id == chapter_id)
            .where(ConsistencyClaim.body_rev == body_rev)
            .where(ConsistencyClaim.status == "accepted")
        )
        claims = list(result.scalars().all())

        # Run rules and collect detected issues
        detected_issues = []
        detected_issues.extend(await self._check_alive_conflicts(db, project_id, claims))
        detected_issues.extend(await self._check_ownership_conflicts(db, project_id, claims))
        detected_issues.extend(await self._check_knowledge_boundary(db, project_id, claims))

        # Upsert issues
        issue_ids = []
        for issue_data in detected_issues:
            issue_id = await self._upsert_issue(db, run_id, project_id, chapter_id, issue_data)
            issue_ids.append(issue_id)

        # Mark stale issues
        await self._mark_stale_issues(db, run_id, project_id, chapter_id, issue_ids)

        return issue_ids

    async def _check_alive_conflicts(
        self,
        db: AsyncSession,
        project_id: str,
        claims: list[ConsistencyClaim],
    ) -> list[dict]:
        """Check for alive=false overlaps in same timeline"""
        issues = []
        alive_claims = [c for c in claims if c.predicate == "alive" and c.object_type == "scalar"]

        # Group by subject and timeline
        by_subject_timeline = {}
        for claim in alive_claims:
            key = (claim.subject_text, claim.timeline_id)
            if key not in by_subject_timeline:
                by_subject_timeline[key] = []
            by_subject_timeline[key].append(claim)

        for (subject, timeline_id), group in by_subject_timeline.items():
            # Skip if timeline order is unknown
            if any(c.story_order is None for c in group):
                continue

            false_claims = [c for c in group if c.object_value == "false" and c.polarity == "positive"]
            if len(false_claims) < 2:
                continue

            # Sort by story_order
            sorted_claims = sorted(false_claims, key=lambda c: c.story_order)

            # Check for overlaps
            for i in range(len(sorted_claims) - 1):
                current = sorted_claims[i]
                next_claim = sorted_claims[i + 1]

                # If current.valid_to_order is None or > next.valid_from_order, conflict
                if current.valid_to_order is None or current.valid_to_order > next_claim.valid_from_order:
                    issues.append({
                        "issue_type": "alive_conflict",
                        "severity": "high",
                        "confidence": 0.95,
                        "description": f"{subject} is marked as not alive in overlapping time intervals",
                        "evidence_claims": [current.id, next_claim.id],
                        "anchor": {"pid": current.paragraph_id or "unknown"},
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
        by_object_timeline = {}
        for claim in ownership_claims:
            if claim.object_entry_id:
                key = (claim.object_entry_id, claim.timeline_id)
                if key not in by_object_timeline:
                    by_object_timeline[key] = []
                by_object_timeline[key].append(claim)

        for (object_id, timeline_id), group in by_object_timeline.items():
            # Skip if timeline order is unknown
            if any(c.story_order is None for c in group):
                continue

            # Sort by story_order
            sorted_claims = sorted(group, key=lambda c: c.story_order)

            # Check for different owners at overlapping times
            for i in range(len(sorted_claims) - 1):
                current = sorted_claims[i]
                next_claim = sorted_claims[i + 1]

                # Different owners?
                if current.subject_entry_id != next_claim.subject_entry_id:
                    # Overlapping intervals?
                    if current.valid_to_order is None or current.valid_to_order > next_claim.valid_from_order:
                        issues.append({
                            "issue_type": "ownership_conflict",
                            "severity": "high",
                            "confidence": 0.90,
                            "description": f"Item owned by different entities at overlapping times",
                            "evidence_claims": [current.id, next_claim.id],
                            "anchor": {"pid": current.paragraph_id or "unknown"},
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

        # Group by subject
        by_subject = {}
        for claim in claims:
            if claim.subject_entry_id:
                if claim.subject_entry_id not in by_subject:
                    by_subject[claim.subject_entry_id] = []
                by_subject[claim.subject_entry_id].append(claim)

        for subject_id, group in by_subject.items():
            # Skip if any claim has unknown story_order
            if any(c.story_order is None for c in group):
                continue

            # Find first mention (introduction)
            sorted_claims = sorted(group, key=lambda c: c.story_order)
            first = sorted_claims[0]

            # Check for claims before first mention
            for claim in sorted_claims[1:]:
                if claim.story_order < first.story_order:
                    issues.append({
                        "issue_type": "knowledge_boundary",
                        "severity": "medium",
                        "confidence": 0.85,
                        "description": f"Knowledge about {claim.subject_text} used before narrative introduction",
                        "evidence_claims": [claim.id, first.id],
                        "anchor": {"pid": claim.paragraph_id or "unknown"},
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
        # Compute fingerprint
        evidence_keys = [str(cid) for cid in issue_data["evidence_claims"]]
        fingerprint = self.compute_issue_fingerprint(issue_data["issue_type"], evidence_keys)

        # Check if issue exists
        result = await db.execute(
            select(GuardIssue)
            .where(GuardIssue.project_id == project_id)
            .where(GuardIssue.fingerprint == fingerprint)
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Update existing issue
            existing.run_id = run_id
            existing.chapter_id = chapter_id
            existing.status = "open"
            existing.stale_at = None
            existing.issue_rev += 1
            existing.updated_at = datetime.now(timezone.utc)
            issue_id = existing.id
        else:
            # Create new issue
            import secrets
            issue_id = f"gi_{secrets.token_hex(12)}"
            issue = GuardIssue(
                id=issue_id,
                project_id=project_id,
                chapter_id=chapter_id,
                run_id=run_id,
                issue_type=issue_data["issue_type"],
                rule_version=self.rule_version,
                fingerprint=fingerprint,
                severity=issue_data["severity"],
                confidence=issue_data["confidence"],
                description=issue_data["description"],
                evidence={},
                anchor=issue_data["anchor"],
                actions=["keep-old", "keep-new", "intentional"],
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
                claim_id=claim.id,
                chapter_id=claim.chapter_id,
                body_rev=claim.body_rev,
                paragraph_id=claim.paragraph_id,
                quote=claim.subject_text,
                content_hash=None,
                metadata={
                    "predicate": claim.predicate,
                    "object_type": claim.object_type,
                    "object_value": claim.object_value,
                },
            )
            db.add(evidence)

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
