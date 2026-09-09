"""Recovery for generation streams lost with the web process."""

import asyncio
from datetime import UTC, datetime, timedelta

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from db.models_usage import GenerationDraft, UsageLog
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

    # ensure_current_quota commits when it changes a user's balance. Commit
    # the draft state as well, including the no-reservation case.
    await db.commit()
    return {"streaming_recovered": len(drafts), "reservations_released": len(expired_reservations)}


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


__all__ = ["STALE_GENERATION_SECONDS", "recover_stale_generation_state", "recover_stale_generation_state_task"]
