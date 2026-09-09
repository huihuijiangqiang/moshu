"""Transactional state machine for resumable long-form generation segments.

The model call intentionally stays outside this module. Workers claim a row,
stream text, checkpoint cumulative snapshots, validate the finished snapshot,
and finally materialize an ordered merge. Every mutation is fenced by the
segment revision so a worker whose lease expired cannot overwrite a retry.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_long_generation import GenerationSegment
from services.long_generation import SegmentCheck, validate_segment_output

DEFAULT_SEGMENT_LEASE_SECONDS = 5 * 60
LEGACY_SEGMENT_LEASE_SECONDS = DEFAULT_SEGMENT_LEASE_SECONDS
MAX_CHECKPOINT_CHARS = 500_000


class SegmentExecutionError(RuntimeError):
    """Base error for a rejected segment state transition."""


class SegmentNotFoundError(SegmentExecutionError):
    pass


class SegmentBusyError(SegmentExecutionError):
    pass


class SegmentLeaseLostError(SegmentExecutionError):
    pass


class SegmentCheckpointConflictError(SegmentExecutionError):
    pass


class SegmentMergeBlockedError(SegmentExecutionError):
    pass


@dataclass(frozen=True)
class SegmentClaim:
    segment_id: str
    chapter_id: str
    segment_index: int
    lease_revision: int
    checkpoint_hash: str
    content_text: str
    target_words: int
    context_manifest: dict
    resumed: bool
    lease_owner: str | None
    lease_expires_at: datetime
    heartbeat_at: datetime


@dataclass(frozen=True)
class SegmentCheckpoint:
    segment_id: str
    lease_revision: int
    checkpoint_hash: str
    generated_words: int


@dataclass(frozen=True)
class SegmentHeartbeat:
    segment_id: str
    lease_revision: int
    lease_owner: str | None
    lease_expires_at: datetime
    heartbeat_at: datetime


@dataclass(frozen=True)
class SegmentAcceptance:
    """Result of fencing a validated segment into the accepted state."""

    segment_id: str
    lease_revision: int
    content_hash: str
    generated_words: int
    idempotent: bool = False


@dataclass(frozen=True)
class SegmentMerge:
    chapter_id: str
    content_text: str
    content_hash: str
    segment_ids: tuple[str, ...]
    generated_words: int


def checkpoint_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _word_count(text: str) -> int:
    # Keep this local to avoid importing the network-facing generation module.
    import re

    return len(re.findall(r"[\u3400-\u9fff]", text)) + len(re.findall(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*", text))


def _require_active_lease(row: GenerationSegment, lease_revision: int) -> None:
    _require_active_lease_at(row, lease_revision)


def _require_active_lease_at(
    row: GenerationSegment,
    lease_revision: int,
    *,
    lease_owner: str | None = None,
    now: datetime | None = None,
) -> None:
    if row.status != "running" or row.revision != lease_revision:
        raise SegmentLeaseLostError(f"segment lease is no longer active: status={row.status}, revision={row.revision}")
    if row.lease_owner and row.lease_owner != lease_owner:
        raise SegmentLeaseLostError("segment lease belongs to another worker")
    current = now or datetime.now(UTC)
    if row.lease_expires_at is not None and _utc(row.lease_expires_at) <= current:
        raise SegmentLeaseLostError("segment lease has expired")


async def claim_segment(
    db: AsyncSession,
    segment_id: str,
    *,
    now: datetime | None = None,
    lease_seconds: int = DEFAULT_SEGMENT_LEASE_SECONDS,
    lease_owner: str | None = None,
) -> SegmentClaim:
    """Claim a pending/failed segment or reclaim an expired running segment.

    ``revision`` is the fencing token. It increments once per claim, including
    retries, while checkpoint writes retain that value. A stale worker holding
    an older revision is therefore rejected even after the row is reclaimed.
    ``updated_at`` is the lease heartbeat and is refreshed by checkpoints.
    """
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")
    current = now or datetime.now(UTC)
    row = await db.scalar(select(GenerationSegment).where(GenerationSegment.id == segment_id).with_for_update())
    if row is None:
        raise SegmentNotFoundError(f"segment not found: {segment_id}")
    if row.status == "running":
        lease_active = (
            _utc(row.lease_expires_at) > current
            if row.lease_expires_at is not None
            else _utc(row.updated_at) > current - timedelta(seconds=LEGACY_SEGMENT_LEASE_SECONDS)
        )
        if lease_active:
            raise SegmentBusyError(f"segment is already running: {segment_id}")
    elif row.status not in {"pending", "failed"}:
        raise SegmentExecutionError(f"segment cannot be claimed from status {row.status}")

    row.status = "running"
    row.revision += 1
    row.error_code = None
    row.completed_at = None
    row.updated_at = current
    row.lease_owner = lease_owner[:100] if lease_owner else None
    row.lease_expires_at = current + timedelta(seconds=lease_seconds)
    row.heartbeat_at = current
    await db.flush()
    content = row.content_text or ""
    return SegmentClaim(
        segment_id=row.id,
        chapter_id=row.chapter_id,
        segment_index=row.segment_index,
        lease_revision=row.revision,
        checkpoint_hash=checkpoint_hash(content),
        content_text=content,
        target_words=row.target_words,
        context_manifest=dict(row.context_manifest or {}),
        resumed=bool(content),
        lease_owner=row.lease_owner,
        lease_expires_at=_utc(row.lease_expires_at),
        heartbeat_at=_utc(row.heartbeat_at),
    )


async def checkpoint_segment(
    db: AsyncSession,
    segment_id: str,
    *,
    lease_revision: int,
    expected_checkpoint_hash: str,
    content_text: str,
    now: datetime | None = None,
    lease_owner: str | None = None,
    lease_seconds: int = DEFAULT_SEGMENT_LEASE_SECONDS,
) -> SegmentCheckpoint:
    """Persist one cumulative stream snapshot with lease and CAS protection."""
    if len(content_text) > MAX_CHECKPOINT_CHARS:
        raise ValueError(f"segment checkpoint exceeds {MAX_CHECKPOINT_CHARS} characters")
    row = await db.scalar(select(GenerationSegment).where(GenerationSegment.id == segment_id).with_for_update())
    if row is None:
        raise SegmentNotFoundError(f"segment not found: {segment_id}")
    current = now or datetime.now(UTC)
    _require_active_lease_at(row, lease_revision, lease_owner=lease_owner, now=current)
    current_content = row.content_text or ""
    if checkpoint_hash(current_content) != expected_checkpoint_hash:
        raise SegmentCheckpointConflictError("checkpoint has advanced; reload before writing")
    if not content_text.startswith(current_content):
        raise SegmentCheckpointConflictError("checkpoint cannot replace or truncate persisted prose")

    row.content_text = content_text
    row.generated_words = _word_count(content_text)
    row.updated_at = current
    row.heartbeat_at = current
    row.lease_expires_at = current + timedelta(seconds=lease_seconds)
    await db.flush()
    return SegmentCheckpoint(
        segment_id=row.id,
        lease_revision=row.revision,
        checkpoint_hash=checkpoint_hash(content_text),
        generated_words=row.generated_words,
    )


async def fail_segment(
    db: AsyncSession,
    segment_id: str,
    *,
    lease_revision: int,
    error_code: str,
    now: datetime | None = None,
    lease_owner: str | None = None,
) -> None:
    """Release a claimed segment for an explicit retry while retaining prose."""
    row = await db.scalar(select(GenerationSegment).where(GenerationSegment.id == segment_id).with_for_update())
    if row is None:
        raise SegmentNotFoundError(f"segment not found: {segment_id}")
    current = now or datetime.now(UTC)
    _require_active_lease_at(row, lease_revision, lease_owner=lease_owner, now=current)
    row.status = "failed"
    row.error_code = (error_code or "segment_failed")[:100]
    row.completed_at = None
    row.updated_at = current
    row.lease_owner = None
    row.lease_expires_at = None
    row.heartbeat_at = current
    await db.flush()


async def validate_claimed_segment(
    db: AsyncSession,
    segment_id: str,
    *,
    lease_revision: int,
    required_terms: Iterable[str] = (),
    min_ratio: float = 0.55,
    max_ratio: float = 1.35,
    now: datetime | None = None,
    lease_owner: str | None = None,
) -> SegmentCheck:
    """Validate a checkpoint and move it to ``ready`` or retryable ``failed``."""
    row = await db.scalar(select(GenerationSegment).where(GenerationSegment.id == segment_id).with_for_update())
    if row is None:
        raise SegmentNotFoundError(f"segment not found: {segment_id}")
    current = now or datetime.now(UTC)
    _require_active_lease_at(row, lease_revision, lease_owner=lease_owner, now=current)
    previous = await db.scalar(
        select(GenerationSegment).where(
            GenerationSegment.chapter_id == row.chapter_id,
            GenerationSegment.segment_index == row.segment_index - 1,
            GenerationSegment.status.in_(("ready", "accepted")),
        )
    )
    check = validate_segment_output(
        row.content_text,
        target_words=row.target_words,
        required_terms=required_terms,
        previous_tail=previous.content_text if previous is not None else "",
        min_ratio=min_ratio,
        max_ratio=max_ratio,
    )
    manifest = dict(row.context_manifest or {})
    manifest["lastValidation"] = {
        "version": 1,
        "status": check.status,
        "checks": [dict(item) for item in check.checks],
        "validatedAt": current.isoformat(),
        "checkpointHash": checkpoint_hash(row.content_text),
    }
    row.context_manifest = manifest
    row.generated_words = _word_count(row.content_text)
    row.status = "failed" if check.blocking else "ready"
    row.error_code = "validation_failed" if check.blocking else None
    row.completed_at = None if check.blocking else current
    row.updated_at = current
    row.lease_owner = None
    row.lease_expires_at = None
    row.heartbeat_at = current
    await db.flush()
    return check


async def accept_ready_segment(
    db: AsyncSession,
    segment_id: str,
    *,
    lease_revision: int,
    now: datetime | None = None,
    lease_owner: str | None = None,
) -> SegmentAcceptance:
    """Accept a validated segment using its claim revision as a fencing token.

    A worker may retry the request after a network timeout.  Repeating the
    request with the same revision is therefore idempotent once the row is
    already accepted.  A different revision is always rejected, including for
    an accepted row, so a reclaimed worker can never acknowledge stale prose.
    """
    row = await db.scalar(select(GenerationSegment).where(GenerationSegment.id == segment_id).with_for_update())
    if row is None:
        raise SegmentNotFoundError(f"segment not found: {segment_id}")
    if row.revision != lease_revision:
        raise SegmentLeaseLostError(
            f"segment revision mismatch: expected={lease_revision}, current={row.revision}"
        )
    if row.status == "accepted":
        return SegmentAcceptance(
            segment_id=row.id,
            lease_revision=row.revision,
            content_hash=checkpoint_hash(row.content_text),
            generated_words=row.generated_words,
            idempotent=True,
        )
    if row.status != "ready":
        raise SegmentExecutionError(f"segment cannot be accepted from status {row.status}")
    if row.lease_owner and row.lease_owner != lease_owner:
        raise SegmentLeaseLostError("segment lease belongs to another worker")
    if not (row.content_text or "").strip():
        raise SegmentExecutionError("cannot accept an empty segment")
    current = now or datetime.now(UTC)
    row.status = "accepted"
    row.error_code = None
    row.completed_at = row.completed_at or current
    row.updated_at = current
    row.lease_owner = None
    row.lease_expires_at = None
    row.heartbeat_at = current
    await db.flush()
    return SegmentAcceptance(
        segment_id=row.id,
        lease_revision=row.revision,
        content_hash=checkpoint_hash(row.content_text),
        generated_words=row.generated_words,
    )


async def heartbeat_segment(
    db: AsyncSession,
    segment_id: str,
    *,
    lease_revision: int,
    lease_owner: str | None = None,
    lease_seconds: int = DEFAULT_SEGMENT_LEASE_SECONDS,
    now: datetime | None = None,
) -> SegmentHeartbeat:
    """Extend an active worker lease without changing the fencing revision."""
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")
    row = await db.scalar(select(GenerationSegment).where(GenerationSegment.id == segment_id).with_for_update())
    if row is None:
        raise SegmentNotFoundError(f"segment not found: {segment_id}")
    current = now or datetime.now(UTC)
    _require_active_lease_at(row, lease_revision, lease_owner=lease_owner, now=current)
    owner = row.lease_owner or lease_owner
    row.lease_owner = owner[:100] if owner else None
    row.heartbeat_at = current
    row.lease_expires_at = current + timedelta(seconds=lease_seconds)
    row.updated_at = current
    await db.flush()
    return SegmentHeartbeat(
        segment_id=row.id,
        lease_revision=row.revision,
        lease_owner=row.lease_owner,
        lease_expires_at=_utc(row.lease_expires_at),
        heartbeat_at=_utc(row.heartbeat_at),
    )


def _merge_boundary(prefix: str, suffix: str, *, max_overlap: int = 500) -> str:
    left = prefix.rstrip()
    right = suffix.lstrip()
    upper = min(max_overlap, len(left), len(right))
    overlap = next(
        (size for size in range(upper, 19, -1) if left.endswith(right[:size])),
        0,
    )
    if overlap:
        right = right[overlap:].lstrip()
    if not left:
        return right
    if not right:
        return left
    return f"{left}\n{right}"


def merge_segment_outputs(rows: Iterable[GenerationSegment]) -> SegmentMerge:
    """Materialize one deterministic merge without writing canonical body data."""
    ordered = sorted(rows, key=lambda row: row.segment_index)
    if not ordered:
        raise SegmentMergeBlockedError("no segments to merge")
    chapter_id = ordered[0].chapter_id
    expected_index = ordered[0].segment_index
    if expected_index != 0:
        raise SegmentMergeBlockedError("segment merge must start at index 0")

    content = ""
    included: list[str] = []
    saw_skipped = False
    for row in ordered:
        if row.chapter_id != chapter_id or row.segment_index != expected_index:
            raise SegmentMergeBlockedError("segments must be contiguous and belong to one chapter")
        expected_index += 1
        if row.status == "skipped":
            saw_skipped = True
            continue
        if saw_skipped:
            raise SegmentMergeBlockedError("active segment cannot follow a skipped segment")
        if row.status not in {"ready", "accepted"}:
            raise SegmentMergeBlockedError(f"segment {row.segment_index} is not mergeable: {row.status}")
        if not (row.content_text or "").strip():
            raise SegmentMergeBlockedError(f"segment {row.segment_index} has no content")
        content = _merge_boundary(content, row.content_text)
        included.append(row.id)

    if not included:
        raise SegmentMergeBlockedError("no completed segment content to merge")
    return SegmentMerge(
        chapter_id=chapter_id,
        content_text=content,
        content_hash=checkpoint_hash(content),
        segment_ids=tuple(included),
        generated_words=_word_count(content),
    )


__all__ = [
    "DEFAULT_SEGMENT_LEASE_SECONDS",
    "LEGACY_SEGMENT_LEASE_SECONDS",
    "MAX_CHECKPOINT_CHARS",
    "SegmentExecutionError",
    "SegmentNotFoundError",
    "SegmentBusyError",
    "SegmentLeaseLostError",
    "SegmentCheckpointConflictError",
    "SegmentMergeBlockedError",
    "SegmentClaim",
    "SegmentCheckpoint",
    "SegmentAcceptance",
    "SegmentHeartbeat",
    "SegmentMerge",
    "checkpoint_hash",
    "claim_segment",
    "checkpoint_segment",
    "fail_segment",
    "validate_claimed_segment",
    "accept_ready_segment",
    "heartbeat_segment",
    "merge_segment_outputs",
]
