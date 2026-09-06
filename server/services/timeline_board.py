"""Read-only projection of accepted temporal claims for the author timeline board."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_consistency_extended import ConsistencyClaim
from db.models_core import Chapter
from services.timeline import author_override_offset, normalize_relative_expression

PlacementStatus = Literal["placed", "review", "ambiguous", "cyclic", "unplaced"]


@dataclass(frozen=True)
class TimelineBoardEvent:
    claim_id: int
    timeline_id: str
    event_ref: str
    chapter_id: str
    chapter_index: int
    chapter_title: str
    time_text: str | None
    story_order: float | None
    placement_status: PlacementStatus
    dependency_status: str
    relation: str | None
    relation_ref: str | None
    source_anchor: str | None
    confidence: float | None
    resolution_source: str | None

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class TimelineBoardLane:
    timeline_id: str
    label: str
    event_count: int
    placed_count: int
    review_count: int
    events: list[TimelineBoardEvent]

    def as_dict(self) -> dict[str, Any]:
        return {
            "timeline_id": self.timeline_id,
            "label": self.label,
            "event_count": self.event_count,
            "placed_count": self.placed_count,
            "review_count": self.review_count,
            "events": [event.as_dict() for event in self.events],
        }


@dataclass(frozen=True)
class TimelineBoard:
    lanes: list[TimelineBoardLane]
    event_count: int
    placed_count: int
    review_count: int
    unplaced_count: int
    story_order_min: float | None
    story_order_max: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "lanes": [lane.as_dict() for lane in self.lanes],
            "event_count": self.event_count,
            "placed_count": self.placed_count,
            "review_count": self.review_count,
            "unplaced_count": self.unplaced_count,
            "story_order_min": self.story_order_min,
            "story_order_max": self.story_order_max,
        }


def _placement_status(claim: ConsistencyClaim, metadata: dict[str, Any]) -> PlacementStatus:
    if claim.story_order is not None:
        return "placed"
    dependency_status = metadata.get("dependency_status")
    if dependency_status in {"ambiguous", "cyclic"}:
        return dependency_status
    resolution = normalize_relative_expression(claim.temporal_anchor_text)
    if resolution is not None and not resolution.is_exact:
        return "review"
    return "unplaced"


def _lane_label(timeline_id: str) -> str:
    if timeline_id == "main":
        return "主线"
    if timeline_id == "unassigned":
        return "未归线"
    return timeline_id


async def build_timeline_board(db: AsyncSession, *, project_id: str) -> TimelineBoard:
    rows = (
        await db.execute(
            select(ConsistencyClaim, Chapter)
            .join(Chapter, Chapter.id == ConsistencyClaim.chapter_id)
            .where(
                ConsistencyClaim.project_id == project_id,
                ConsistencyClaim.status == "accepted",
                Chapter.deleted_at.is_(None),
                or_(
                    ConsistencyClaim.temporal_event_ref.isnot(None),
                    ConsistencyClaim.temporal_anchor_text.isnot(None),
                ),
            )
            .order_by(Chapter.idx, ConsistencyClaim.id)
        )
    ).all()

    by_lane: dict[str, list[TimelineBoardEvent]] = {}
    for claim, chapter in rows:
        metadata = claim.temporal_resolution if isinstance(claim.temporal_resolution, dict) else {}
        timeline_id = claim.timeline_id or "unassigned"
        override = author_override_offset(
            {
                "temporal_resolution": metadata,
                "temporal_anchor_text": claim.temporal_anchor_text,
                "temporal_relation": claim.temporal_relation,
            }
        )
        event = TimelineBoardEvent(
            claim_id=claim.id,
            timeline_id=timeline_id,
            event_ref=claim.temporal_event_ref or f"{claim.subject_text} · {claim.predicate}",
            chapter_id=chapter.id,
            chapter_index=chapter.idx,
            chapter_title=chapter.title,
            time_text=claim.temporal_anchor_text,
            story_order=float(claim.story_order) if claim.story_order is not None else None,
            placement_status=_placement_status(claim, metadata),
            dependency_status=str(metadata.get("dependency_status") or "unknown"),
            relation=claim.temporal_relation,
            relation_ref=claim.temporal_relation_ref,
            source_anchor=claim.source_anchor,
            confidence=float(claim.order_confidence) if claim.order_confidence is not None else None,
            resolution_source="author" if override is not None else (
                str(metadata["resolution_source"]) if metadata.get("resolution_source") else None
            ),
        )
        by_lane.setdefault(timeline_id, []).append(event)

    lanes: list[TimelineBoardLane] = []
    for timeline_id, events in sorted(by_lane.items(), key=lambda item: (item[0] != "main", item[0])):
        events.sort(
            key=lambda event: (
                event.story_order is None,
                event.story_order if event.story_order is not None else event.chapter_index,
                event.chapter_index,
                event.claim_id,
            )
        )
        lanes.append(
            TimelineBoardLane(
                timeline_id=timeline_id,
                label=_lane_label(timeline_id),
                event_count=len(events),
                placed_count=sum(event.placement_status == "placed" for event in events),
                review_count=sum(event.placement_status == "review" for event in events),
                events=events,
            )
        )

    all_events = [event for lane in lanes for event in lane.events]
    orders = [event.story_order for event in all_events if event.story_order is not None]
    return TimelineBoard(
        lanes=lanes,
        event_count=len(all_events),
        placed_count=sum(event.placement_status == "placed" for event in all_events),
        review_count=sum(event.placement_status == "review" for event in all_events),
        unplaced_count=sum(event.placement_status != "placed" for event in all_events),
        story_order_min=min(orders) if orders else None,
        story_order_max=max(orders) if orders else None,
    )


__all__ = ["TimelineBoard", "TimelineBoardEvent", "TimelineBoardLane", "build_timeline_board"]
