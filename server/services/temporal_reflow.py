"""Project-wide temporal dependency reflow.

Changing an upstream anchor must invalidate and recompute every downstream relative
claim. This service performs that operation in one transaction while locking claims in
a deterministic order. It never invents a point for fuzzy ranges: those remain NULL in
``story_order`` and carry an auditable resolution status instead.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_consistency_extended import ConsistencyClaim, ConsistencyRun
from db.models_core import ChapterBody, Project
from services.consistency import PIPELINE_VERSION, assign_narrative_positions
from services.outbox import OutboxService
from services.timeline import (
    assign_story_orders,
    author_override_offset,
    build_temporal_dependency_graph,
    merge_author_temporal_metadata,
    normalize_relative_expression,
)


@dataclass
class TemporalReflowResult:
    claims_examined: int = 0
    claims_changed: int = 0
    affected_chapter_ids: list[str] = field(default_factory=list)
    resolved: int = 0
    unresolved: int = 0
    ambiguous: int = 0
    cyclic: int = 0
    cycles: list[list[str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "claims_examined": self.claims_examined,
            "claims_changed": self.claims_changed,
            "affected_chapter_ids": self.affected_chapter_ids,
            "resolved": self.resolved,
            "unresolved": self.unresolved,
            "ambiguous": self.ambiguous,
            "cyclic": self.cyclic,
            "cycles": self.cycles,
        }


def _claim_dict(claim: ConsistencyClaim) -> dict[str, Any]:
    return {
        "id": claim.id,
        "fingerprint": claim.fingerprint,
        "chapter_id": claim.chapter_id,
        "subject_entry_id": claim.subject_entry_id,
        "subject_text": claim.subject_text,
        "predicate": claim.predicate,
        "object_entry_id": claim.object_entry_id,
        "object_value": claim.object_value,
        "timeline_id": claim.timeline_id,
        "temporal_anchor_text": claim.temporal_anchor_text,
        "temporal_anchor_value": claim.temporal_anchor_value,
        "temporal_event_ref": claim.temporal_event_ref,
        "temporal_relation": claim.temporal_relation,
        "temporal_relation_ref": claim.temporal_relation_ref,
        "temporal_resolution": claim.temporal_resolution,
        "order_basis": claim.order_basis,
        "order_confidence": (
            float(claim.order_confidence) if claim.order_confidence is not None else None
        ),
        "story_order": float(claim.story_order) if claim.story_order is not None else None,
        "valid_from_order": (
            float(claim.valid_from_order) if claim.valid_from_order is not None else None
        ),
        "valid_to_order": (
            float(claim.valid_to_order) if claim.valid_to_order is not None else None
        ),
    }


def _same_number(left: Decimal | float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return abs(float(left) - float(right)) < 0.00000001


async def reflow_project_timeline(
    db: AsyncSession,
    *,
    project_id: str,
) -> TemporalReflowResult:
    """Recompute all accepted project claims and persist dependency diagnostics.

    The caller owns commit/rollback. Row locks are acquired by claim id so concurrent
    chapter extraction jobs cannot interleave two partial project timelines.
    """
    await lock_project_timeline(db, project_id=project_id)
    rows = list(
        (
            await db.execute(
                select(ConsistencyClaim)
                .where(
                    ConsistencyClaim.project_id == project_id,
                    ConsistencyClaim.status == "accepted",
                )
                .order_by(ConsistencyClaim.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    raw = [_claim_dict(row) for row in rows]
    # Compatibility window for claims produced before order_basis was persisted.
    # Preserve an existing accepted event order as a read-only root so enabling
    # reflow does not erase an established project timeline. New extraction never
    # enters this branch because it always records its basis. Remove this fallback
    # only after a migration has classified every legacy anchor as confirmed or
    # unresolved; until then silently clearing those orders is the riskier change.
    legacy_anchor_ids = {
        claim["id"]
        for claim in raw
        if claim.get("order_basis") is None
        and claim.get("story_order") is not None
        and claim.get("timeline_id")
        and claim.get("temporal_event_ref")
    }
    legacy_anchors = [claim for claim in raw if claim["id"] in legacy_anchor_ids]
    ordered = assign_story_orders(raw, known_anchors=legacy_anchors)
    for previous, claim in zip(raw, ordered, strict=True):
        if claim["id"] in legacy_anchor_ids:
            claim["story_order"] = previous["story_order"]
            claim["valid_from_order"] = previous["valid_from_order"]
            claim["valid_to_order"] = previous["valid_to_order"]
            continue
        old_order = previous.get("story_order")
        new_order = claim.get("story_order")
        if new_order is None:
            claim["valid_from_order"] = None
            claim["valid_to_order"] = None
            continue
        if old_order is None:
            claim["valid_from_order"] = None
            claim["valid_to_order"] = None
            continue
        delta = float(new_order) - float(old_order)
        claim["valid_from_order"] = (
            float(previous["valid_from_order"]) + delta
            if previous.get("valid_from_order") is not None
            else None
        )
        claim["valid_to_order"] = (
            float(previous["valid_to_order"]) + delta
            if previous.get("valid_to_order") is not None
            else None
        )
    graph = build_temporal_dependency_graph(ordered)
    positioned = assign_narrative_positions(ordered)
    for previous, claim in zip(raw, positioned, strict=True):
        if claim["id"] in legacy_anchor_ids:
            claim["story_order"] = previous["story_order"]
            claim["valid_from_order"] = previous["valid_from_order"]
            claim["valid_to_order"] = previous["valid_to_order"]
    edge_by_node = {edge["from"]: edge for edge in graph["edges"]}

    changed_chapters: set[str] = set()
    status_counts = {"resolved": 0, "unresolved": 0, "ambiguous": 0, "cyclic": 0}
    changed = 0
    for row, claim in zip(rows, positioned, strict=True):
        node_id = str(row.id)
        edge = edge_by_node.get(node_id)
        resolution = normalize_relative_expression(row.temporal_anchor_text)
        metadata: dict[str, Any] | None = None
        if row.order_basis == "relative_to_anchor":
            metadata = resolution.as_dict() if resolution is not None else {
                "original": row.temporal_anchor_text,
                "normalized": None,
                "precision": "unparsed",
                "is_exact": False,
                "offset_min_seconds": None,
                "offset_max_seconds": None,
            }
            status = edge["status"] if edge is not None else "unresolved"
            metadata.update(
                {
                    "dependency_status": status,
                    "relation": row.temporal_relation,
                    "relation_ref": row.temporal_relation_ref,
                }
            )
            metadata = merge_author_temporal_metadata(row.temporal_resolution, metadata)
            override_offset = author_override_offset(claim)
            if metadata is not None and override_offset is not None:
                metadata["effective_offset_seconds"] = override_offset
                metadata["resolution_source"] = "author"
            status_counts[status] += 1

        row_changed = not (
            _same_number(row.story_order, claim.get("story_order"))
            and _same_number(row.valid_from_order, claim.get("valid_from_order"))
            and _same_number(row.valid_to_order, claim.get("valid_to_order"))
            and row.temporal_resolution == metadata
        )
        if not row_changed:
            continue
        row.story_order = claim.get("story_order")
        row.valid_from_order = claim.get("valid_from_order")
        row.valid_to_order = claim.get("valid_to_order")
        row.temporal_resolution = metadata
        changed += 1
        if row.chapter_id:
            changed_chapters.add(row.chapter_id)

    await db.flush()
    return TemporalReflowResult(
        claims_examined=len(rows),
        claims_changed=changed,
        affected_chapter_ids=sorted(changed_chapters),
        resolved=status_counts["resolved"],
        unresolved=status_counts["unresolved"],
        ambiguous=status_counts["ambiguous"],
        cyclic=status_counts["cyclic"],
        cycles=graph["cycles"],
    )


async def lock_project_timeline(db: AsyncSession, *, project_id: str) -> None:
    """Serialize every claim mutation that can change a project's time graph.

    Callers that mutate claims must acquire this lock before their first claim DML.
    Reflow then takes claim row locks in id order. Keeping that order prevents two
    chapter extractions from each holding claim locks while waiting on one another's
    project-wide graph snapshot.
    """
    await db.execute(select(Project.id).where(Project.id == project_id).with_for_update())


async def enqueue_temporal_rescans(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_ids: list[str],
    cause_id: str,
    exclude_chapter_id: str | None = None,
) -> list[int]:
    """Queue rule-only rescans for changed chapters using their current runs.

    The returned run ids intentionally exclude chapters without a completed run for
    their current body revision; API/UI rescan counts therefore describe work that
    was actually queued, not every chapter touched by reflow.
    """
    targets = sorted(set(chapter_ids) - ({exclude_chapter_id} if exclude_chapter_id else set()))
    if not targets:
        return []
    rows = (
        await db.execute(
            select(ConsistencyRun, ChapterBody)
            .join(ChapterBody, ChapterBody.chapter_id == ConsistencyRun.chapter_id)
            .where(
                ConsistencyRun.project_id == project_id,
                ConsistencyRun.chapter_id.in_(targets),
                ConsistencyRun.body_rev == ChapterBody.rev,
                ConsistencyRun.pipeline_version == PIPELINE_VERSION,
                ConsistencyRun.status == "completed",
            )
            .order_by(ConsistencyRun.chapter_id, ConsistencyRun.id.desc())
        )
    ).all()
    latest: dict[str, tuple[ConsistencyRun, ChapterBody]] = {}
    for run, body in rows:
        latest.setdefault(run.chapter_id, (run, body))

    run_ids: list[int] = []
    for chapter_id in targets:
        target = latest.get(chapter_id)
        if target is None:
            continue
        run, body = target
        digest = hashlib.sha256(f"{cause_id}:{chapter_id}:{body.rev}".encode()).hexdigest()[:40]
        await OutboxService.enqueue(
            db,
            topic="consistency.timeline_rescan",
            aggregate_id=f"tr_{digest}",
            aggregate_rev=body.rev,
            payload={
                "project_id": project_id,
                "chapter_id": chapter_id,
                "body_rev": body.rev,
                "run_id": run.id,
                "cause_id": cause_id,
            },
        )
        run_ids.append(run.id)
    return run_ids


__all__ = [
    "TemporalReflowResult",
    "enqueue_temporal_rescans",
    "lock_project_timeline",
    "reflow_project_timeline",
]
