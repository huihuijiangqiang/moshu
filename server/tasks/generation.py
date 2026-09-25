"""Recovery for generation streams lost with the web process."""

import asyncio
from datetime import UTC, datetime, timedelta

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from db.models_long_generation import GenerationSegment
from db.models_usage import GenerationDraft, UsageLog
from services.long_generation_runner import LongSegmentOptions, execute_long_segment
from services.usage import ensure_current_quota

engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# A browser may legitimately keep one request open for several minutes, but a
# draft older than this without a heartbeat can no longer receive SSE events.
STALE_GENERATION_SECONDS = 30 * 60


async def recover_stale_generation_state(
    db: AsyncSession,
    *,
    stale_seconds: int = STALE_GENERATION_SECONDS,
    now: datetime | None = None,
) -> dict[str, int]:
    """Make abandoned streams and reservations visible/recoverable.

    Generation is intentionally an HTTP SSE operation, so a process kill can
    leave a ``streaming`` row without running ``finally``. This task is the
    durable lease expiry for that state; partial content remains in the draft
    and the normal continuation endpoint can resume it.
    """
    current = now or datetime.now(UTC)
    cutoff = current - timedelta(seconds=max(1, stale_seconds))
    drafts = list(
        (
            await db.execute(
                select(GenerationDraft)
                .where(
                    GenerationDraft.status == "streaming",
                    GenerationDraft.updated_at < cutoff,
                )
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for draft in drafts:
        draft.status = "failed"
        draft.error_code = "stream_timeout"
        draft.updated_at = current

    expired_reservations = list(
        (
            await db.execute(
                select(UsageLog.id, UsageLog.user_id)
                .where(
                    UsageLog.status == "reserved",
                    UsageLog.reservation_expires_at.is_not(None),
                    UsageLog.reservation_expires_at <= current,
                )
            )
        )
        .all()
    )
    expired_users = {user_id for _, user_id in expired_reservations}
    for user_id in expired_users:
        await ensure_current_quota(db, user_id)

    expired_segments = list(
        (
            await db.execute(
                select(GenerationSegment)
                .where(
                    GenerationSegment.status == "running",
                    GenerationSegment.lease_expires_at.is_not(None),
                    GenerationSegment.lease_expires_at <= current,
                )
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for segment in expired_segments:
        segment.status = "failed"
        segment.error_code = "segment_lease_expired"
        segment.lease_owner = None
        segment.lease_expires_at = None
        segment.heartbeat_at = current
        segment.updated_at = current

    # ensure_current_quota commits when it changes a user's balance. Commit
    # the draft state as well, including the no-reservation case.
    await db.commit()
    return {
        "streaming_recovered": len(drafts),
        "reservations_released": len(expired_reservations),
        "segments_recovered": len(expired_segments),
    }


def run_async(coro):
    """Run one Celery coroutine on the worker's persistent event loop.

    Celery's prefork worker may not create a loop before invoking the first
    task. ``get_event_loop`` creates the loop on Python 3.12 (the supported
    runtime) and, unlike ``asyncio.run``, keeps SQLAlchemy's async engine bound
    to the same loop for subsequent tasks in that worker process.
    """
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(coro)


@shared_task(name="generation.recover_stale_state")
def recover_stale_generation_state_task():
    async def run():
        async with AsyncSessionLocal() as db:
            return await recover_stale_generation_state(db)

    return run_async(run())


@shared_task(name="generation.generate_long_segment")
def generate_long_segment_task(
    segment_id: str,
    user_id: str,
    lease_revision: int,
    lease_owner: str,
):
    """Generate one claimed long-form segment in a durable worker.

    The API claims the segment before publishing this task.  The revision and
    owner are passed through as a fencing token so a late worker cannot write
    into a reclaimed retry.
    """

    async def run():
        async with AsyncSessionLocal() as db:
            segment = await db.get(GenerationSegment, segment_id)
            if segment is None:
                return {"status": "missing", "segmentId": segment_id}
            raw = segment.context_manifest.get("generationRequest", {}) if isinstance(segment.context_manifest, dict) else {}
            options = LongSegmentOptions(
                model=str(raw.get("model") or "basic"),
                provider_model=str(raw["providerModel"]) if raw.get("providerModel") else None,
                use_style_profile=bool(raw.get("useStyleProfile", True)),
                dialogue_density=str(raw.get("dialogueDensity") or "mid"),
                context_mode=str(raw.get("contextMode") or "smart"),
                instruction=str(raw.get("instruction") or ""),
            )
            return await execute_long_segment(
                db,
                segment_id=segment_id,
                user_id=user_id,
                lease_revision=lease_revision,
                lease_owner=lease_owner,
                options=options,
            )

    return run_async(run())


__all__ = ["STALE_GENERATION_SECONDS", "recover_stale_generation_state", "recover_stale_generation_state_task", "generate_long_segment_task"]
