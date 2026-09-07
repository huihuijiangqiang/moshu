"""Author decisions for fuzzy relative-time claims."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_consistency_extended import ConsistencyClaim
from db.models_core import Chapter
from services.temporal_reflow import (
    TemporalReflowResult,
    enqueue_temporal_rescans,
    lock_project_timeline,
    reflow_project_timeline,
)
from services.timeline import (
    author_override_offset,
    normalize_relative_expression,
)


class TemporalDecisionError(ValueError):
    pass


class TemporalDecisionConflictError(TemporalDecisionError):
    def __init__(self, message: str, *, current_version: int):
        super().__init__(message)
        self.current_version = current_version


class TemporalClaimNotFoundError(TemporalDecisionError):
    pass


MAX_OVERRIDE_HISTORY = 20


@dataclass(frozen=True)
class TemporalReviewItem:
    claim_id: int
    chapter_id: str | None
    chapter_index: int | None
    chapter_title: str | None
    event_ref: str | None
    relation: str | None
    relation_ref: str | None
    original: str
    normalized: str
    offset_min_seconds: float
    offset_max_seconds: float
    dependency_status: str
    override_seconds: float | None
    override_version: int

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class TemporalDecisionResult:
    item: TemporalReviewItem
    reflow: TemporalReflowResult
    rescan_run_ids: list[int]


def _review_item(
    claim: ConsistencyClaim,
    chapter: Chapter | None,
) -> TemporalReviewItem | None:
    resolution = normalize_relative_expression(claim.temporal_anchor_text)
    if resolution is None or resolution.is_exact:
        return None
    metadata = claim.temporal_resolution if isinstance(claim.temporal_resolution, dict) else {}
    override = author_override_offset(
        {
            "temporal_resolution": metadata,
            "temporal_anchor_text": claim.temporal_anchor_text,
            "temporal_relation": claim.temporal_relation,
        }
    )
    return TemporalReviewItem(
        claim_id=claim.id,
        chapter_id=claim.chapter_id,
        chapter_index=chapter.idx if chapter else None,
        chapter_title=chapter.title if chapter else None,
        event_ref=claim.temporal_event_ref,
        relation=claim.temporal_relation,
        relation_ref=claim.temporal_relation_ref,
        original=resolution.original,
        normalized=resolution.normalized,
        offset_min_seconds=resolution.offset_min_seconds,
        offset_max_seconds=resolution.offset_max_seconds,
        dependency_status=str(metadata.get("dependency_status") or "unresolved"),
        override_seconds=override,
        override_version=int(metadata.get("author_override_version") or 0),
    )


async def list_temporal_reviews(
    db: AsyncSession,
    *,
    project_id: str,
) -> list[TemporalReviewItem]:
    rows = (
        await db.execute(
            select(ConsistencyClaim, Chapter)
            .outerjoin(Chapter, Chapter.id == ConsistencyClaim.chapter_id)
            .where(
                ConsistencyClaim.project_id == project_id,
                ConsistencyClaim.status == "accepted",
                ConsistencyClaim.order_basis == "relative_to_anchor",
            )
            .order_by(Chapter.idx, ConsistencyClaim.id)
        )
    ).all()
    return [item for claim, chapter in rows if (item := _review_item(claim, chapter))]


async def decide_temporal_review(
    db: AsyncSession,
    *,
    project_id: str,
    claim_id: int,
    actor_id: str,
    action: Literal["confirm", "clear"],
    expected_version: int,
    offset_seconds: float | None = None,
) -> TemporalDecisionResult:
    await lock_project_timeline(db, project_id=project_id)
    claim = (
        await db.execute(
            select(ConsistencyClaim)
            .where(
                ConsistencyClaim.id == claim_id,
                ConsistencyClaim.project_id == project_id,
                ConsistencyClaim.status == "accepted",
                ConsistencyClaim.order_basis == "relative_to_anchor",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if claim is None:
        raise TemporalClaimNotFoundError("Temporal claim not found")

    resolution = normalize_relative_expression(claim.temporal_anchor_text)
    if resolution is None or resolution.is_exact:
        raise TemporalDecisionError("Only fuzzy relative-time claims can be reviewed")
    metadata = claim.temporal_resolution if isinstance(claim.temporal_resolution, dict) else {}
    current_version = int(metadata.get("author_override_version") or 0)
    if current_version != expected_version:
        raise TemporalDecisionConflictError(
            "Temporal decision has changed",
            current_version=current_version,
        )

    if action == "confirm":
        probe = {
            **resolution.as_dict(),
            "author_override": {"offset_seconds": offset_seconds},
        }
        valid_offset = author_override_offset(
            {
                "temporal_resolution": probe,
                "temporal_anchor_text": claim.temporal_anchor_text,
                "temporal_relation": claim.temporal_relation,
            }
        )
        if valid_offset is None:
            raise TemporalDecisionError("Offset must be inside the parsed range and match its relation")
        current_offset = author_override_offset(
            {
                "temporal_resolution": metadata,
                "temporal_anchor_text": claim.temporal_anchor_text,
                "temporal_relation": claim.temporal_relation,
            }
        )
        if current_offset == valid_offset:
            changed = False
        else:
            changed = True
            next_override: dict[str, Any] | None = {
                "offset_seconds": valid_offset,
                "confirmed_by": actor_id,
            }
    elif action == "clear":
        changed = metadata.get("author_override") is not None
        next_override = None
    else:
        raise TemporalDecisionError("Unsupported temporal decision")

    if changed:
        next_version = current_version + 1
        decided_at = datetime.now(timezone.utc).isoformat()
        stored_history = metadata.get("author_override_history")
        history = [entry for entry in stored_history if isinstance(entry, dict)] if isinstance(
            stored_history, list
        ) else []
        history.append(
            {
                "version": next_version,
                "action": action,
                "offset_seconds": next_override.get("offset_seconds") if next_override else None,
                "actor_id": actor_id,
                "decided_at": decided_at,
            }
        )
        history = history[-MAX_OVERRIDE_HISTORY:]
        replacement = resolution.as_dict()
        replacement.update(
            {
                "author_override": (
                    {**next_override, "confirmed_at": decided_at} if next_override else None
                ),
                "author_override_version": next_version,
                "author_override_history": history,
            }
        )
        claim.temporal_resolution = replacement
        await db.flush()

    reflow = await reflow_project_timeline(db, project_id=project_id)
    version = int((claim.temporal_resolution or {}).get("author_override_version") or 0)
    rescan_ids = await enqueue_temporal_rescans(
        db,
        project_id=project_id,
        chapter_ids=reflow.affected_chapter_ids,
        cause_id=f"temporal-decision-{claim_id}-v{version}",
    )
    chapter = await db.get(Chapter, claim.chapter_id) if claim.chapter_id else None
    item = _review_item(claim, chapter)
    if item is None:
        raise TemporalDecisionError("Temporal claim is no longer reviewable")
    return TemporalDecisionResult(item=item, reflow=reflow, rescan_run_ids=rescan_ids)


__all__ = [
    "TemporalClaimNotFoundError",
    "TemporalDecisionConflictError",
    "TemporalDecisionError",
    "TemporalDecisionResult",
    "TemporalReviewItem",
    "MAX_OVERRIDE_HISTORY",
    "decide_temporal_review",
    "list_temporal_reviews",
]
