"""Grounded, fail-open second-pass review for deterministic Guard issues."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_consistency_extended import ConsistencyClaim
from db.models_core import ChapterVersion
from db.models_guard import GuardIssue
from providers.consistency import ARBITRATION_VERSION, ArbitrationDecision
from services.chunking import html_to_paragraphs

MAX_EVIDENCE_QUOTE_CHARS = 600
_SOURCE_ANCHOR_RE = re.compile(r"^P(?P<position>\d+)$", re.IGNORECASE)


def _paragraph_quote(source_anchor: str | None, paragraphs: list[str]) -> str | None:
    if not source_anchor:
        return None
    match = _SOURCE_ANCHOR_RE.fullmatch(source_anchor.strip())
    if not match:
        return None
    position = int(match.group("position"))
    if position >= len(paragraphs):
        return None
    return paragraphs[position][:MAX_EVIDENCE_QUOTE_CHARS]


async def load_pending_arbitration_cases(
    db: AsyncSession,
    *,
    run_id: int,
) -> tuple[list[GuardIssue], list[dict[str, Any]]]:
    issues = list(
        (
            await db.execute(
                select(GuardIssue)
                .where(
                    GuardIssue.run_id == run_id,
                    GuardIssue.arbitration_status == "pending",
                    GuardIssue.false_positive.is_(False),
                )
                .order_by(GuardIssue.id)
            )
        )
        .scalars()
        .all()
    )
    claim_ids = {
        int(claim_id)
        for issue in issues
        for claim_id in (issue.evidence or {}).get("claim_ids", [])
        if isinstance(claim_id, int)
    }
    claims = (
        list((await db.execute(select(ConsistencyClaim).where(ConsistencyClaim.id.in_(claim_ids)))).scalars().all())
        if claim_ids
        else []
    )
    claims_by_id = {claim.id: claim for claim in claims}

    version_keys = {(claim.chapter_id, claim.body_rev) for claim in claims if claim.chapter_id and claim.body_rev}
    versions: dict[tuple[str, int], list[str]] = {}
    for chapter_id, body_rev in version_keys:
        snapshot = await db.scalar(
            select(ChapterVersion).where(
                ChapterVersion.chapter_id == chapter_id,
                ChapterVersion.rev == body_rev,
            )
        )
        if snapshot:
            versions[(chapter_id, body_rev)] = html_to_paragraphs(snapshot.content_html)

    cases = []
    for issue in issues:
        evidence = []
        for claim_id in (issue.evidence or {}).get("claim_ids", []):
            claim = claims_by_id.get(claim_id)
            if not claim:
                continue
            paragraphs = versions.get((claim.chapter_id, claim.body_rev), [])
            evidence.append(
                {
                    "claim_id": claim.id,
                    "chapter_id": claim.chapter_id,
                    "body_rev": claim.body_rev,
                    "source_anchor": claim.source_anchor,
                    "quote": _paragraph_quote(claim.source_anchor, paragraphs),
                    "subject": claim.subject_text,
                    "predicate": claim.predicate,
                    "object": claim.object_value,
                    "certainty": claim.certainty,
                    "timeline_id": claim.timeline_id,
                    "story_order": (float(claim.story_order) if claim.story_order is not None else None),
                }
            )
        cases.append(
            {
                "case_id": issue.fingerprint,
                "issue_type": issue.issue_type,
                "rule_description": issue.description,
                "evidence": evidence,
            }
        )
    return issues, cases


def apply_arbitration_results(
    issues: list[GuardIssue],
    decisions: dict[str, ArbitrationDecision],
    *,
    model: str,
) -> None:
    now = datetime.now(timezone.utc)
    for issue in issues:
        if issue.arbitration_status != "pending":
            continue
        decision = decisions.get(issue.fingerprint)
        if decision is None:
            issue.arbitration_status = "failed"
            issue.arbitration_error = "model response omitted this issue"
            issue.arbitrated_at = now
            continue
        issue.arbitration_status = decision.verdict
        issue.arbitration_confidence = decision.confidence
        issue.arbitration_rationale = decision.rationale
        issue.arbitration_model = model
        issue.arbitration_version = ARBITRATION_VERSION
        issue.arbitration_error = None
        issue.arbitrated_at = now


def apply_arbitration_failure(issues: list[GuardIssue], error: Exception) -> None:
    now = datetime.now(timezone.utc)
    detail = f"{type(error).__name__}: {error}"[:2000]
    for issue in issues:
        if issue.arbitration_status != "pending":
            continue
        issue.arbitration_status = "failed"
        issue.arbitration_confidence = None
        issue.arbitration_rationale = None
        issue.arbitration_model = None
        issue.arbitration_version = ARBITRATION_VERSION
        issue.arbitration_error = detail
        issue.arbitrated_at = now


__all__ = [
    "MAX_EVIDENCE_QUOTE_CHARS",
    "apply_arbitration_failure",
    "apply_arbitration_results",
    "load_pending_arbitration_cases",
]
