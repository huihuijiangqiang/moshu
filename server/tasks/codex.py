"""Codex embedding backfill with bounded retries and persistent dead letters."""
import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx
from celery import shared_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config import settings
from db.models_embedding import CodexEmbeddingJob
from services.chapter_chunks import embed_pending_chapter_chunks, mark_chapter_chunks_failed
from services.codex import count_stale_entries
from services.codex_embedding import embed_missing_codex_entries
from services.embedding import EmbeddingProviderError, GatewayEmbeddingProvider
from services.embedding_jobs import (
    begin_attempt,
    complete_job,
    fail_job,
    record_dispatch_failure,
)
from services.provider_usage import record_platform_usage

engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
logger = logging.getLogger(__name__)

#: 视为「网关暂时不可用、值得重试」的异常。httpx.HTTPError 覆盖连接与状态码错误。
RETRYABLE_ERRORS = (EmbeddingProviderError, OSError, httpx.HTTPError)
STALE_QUEUE_SECONDS = 120
MAX_DISPATCH_ATTEMPTS = 5


def run_async(coro):
    """与 tasks.consistency 同一套同步包装。"""
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(coro)


async def _backfill_async(
    project_id: str,
    batch_size: int,
    *,
    attempt: int = 1,
    task_id: str | None = None,
    exhausted: bool = False,
) -> dict:
    async with AsyncSessionLocal() as db:
        await begin_attempt(db, project_id, attempt=attempt, task_id=task_id)
        await db.commit()
        provider = GatewayEmbeddingProvider()
        try:
            embedded = await embed_missing_codex_entries(
                db,
                provider,
                project_id=project_id,
                batch_size=batch_size,
                commit_each_batch=True,
            )
            remaining = await count_stale_entries(db, project_id)
            await complete_job(
                db,
                project_id,
                embedded_count=embedded,
                remaining_count=remaining,
            )
            await db.commit()
            await _persist_embedding_usage(project_id, provider, task_id)
        except Exception as error:
            await db.rollback()
            remaining = await count_stale_entries(db, project_id)
            retryable = isinstance(error, RETRYABLE_ERRORS)
            await fail_job(
                db,
                project_id,
                attempt=attempt,
                error=error,
                remaining_count=remaining,
                exhausted=exhausted or not retryable,
            )
            await db.commit()
            await _persist_embedding_usage(project_id, provider, task_id)
            raise
        return {
            "status": "success",
            "project_id": project_id,
            "embedded_count": embedded,
            "remaining_count": remaining,
        }


async def _persist_embedding_usage(
    project_id: str,
    provider: GatewayEmbeddingProvider,
    task_id: str | None,
) -> None:
    events = getattr(provider, "usage_events", None)
    if not isinstance(events, list) or not events:
        return
    try:
        async with AsyncSessionLocal() as usage_db:
            await record_platform_usage(
                usage_db,
                project_id=project_id,
                feature="embedding",
                events=events,
                task_id=task_id,
            )
            await usage_db.commit()
    except Exception:
        # Embedding lifecycle state is authoritative; telemetry must not change it.
        logger.exception(
            "failed to persist embedding usage for project %s task %s", project_id, task_id
        )
        return


async def _backfill_chapter_chunks_async(
    project_id: str,
    chapter_id: str,
    body_rev: int,
    batch_size: int,
    *,
    task_id: str | None = None,
    exhausted: bool = False,
) -> dict:
    async with AsyncSessionLocal() as db:
        provider = GatewayEmbeddingProvider()
        try:
            embedded = await embed_pending_chapter_chunks(
                db,
                provider,
                project_id=project_id,
                chapter_id=chapter_id,
                body_rev=body_rev,
                batch_size=batch_size,
            )
            await db.commit()
            await _persist_embedding_usage(project_id, provider, task_id)
            return {
                "status": "success",
                "project_id": project_id,
                "chapter_id": chapter_id,
                "body_rev": body_rev,
                "embedded_count": embedded,
            }
        except Exception as error:
            await db.rollback()
            if exhausted or not isinstance(error, RETRYABLE_ERRORS):
                await mark_chapter_chunks_failed(
                    db,
                    project_id=project_id,
                    chapter_id=chapter_id,
                    body_rev=body_rev,
                    error=error,
                )
                await db.commit()
            await _persist_embedding_usage(project_id, provider, task_id)
            raise


@shared_task(
    bind=True,
    name="codex.backfill_chapter_chunks",
    autoretry_for=RETRYABLE_ERRORS,
    max_retries=5,
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def backfill_chapter_chunks_task(
    self, project_id: str, chapter_id: str, body_rev: int, batch_size: int = 32
):
    """Embed only the current revision's pending body chunks."""
    return run_async(
        _backfill_chapter_chunks_async(
            project_id,
            chapter_id,
            body_rev,
            batch_size,
            task_id=getattr(self.request, "id", None),
            exhausted=int(getattr(self.request, "retries", 0)) >= self.max_retries,
        )
    )


@shared_task(
    bind=True,
    name="codex.backfill_embeddings",
    autoretry_for=RETRYABLE_ERRORS,
    max_retries=5,
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def backfill_codex_embeddings_task(self, project_id: str, batch_size: int = 32):
    """Backfill stale entries and persist every attempt, including exhaustion."""
    retries = int(getattr(self.request, "retries", 0))
    return run_async(
        _backfill_async(
            project_id,
            batch_size,
            attempt=retries + 1,
            task_id=getattr(self.request, "id", None),
            exhausted=retries >= self.max_retries,
        )
    )


async def _recover_stale_embedding_jobs_async(
    *,
    stale_seconds: int = STALE_QUEUE_SECONDS,
    batch_size: int = 20,
) -> dict:
    """Re-dispatch jobs stranded between the database commit and broker publish."""
    from celery_app import celery_app

    cutoff = datetime.now(UTC) - timedelta(seconds=stale_seconds)
    async with AsyncSessionLocal() as db:
        jobs = list(
            (
                await db.execute(
                    select(CodexEmbeddingJob)
                    .where(
                        CodexEmbeddingJob.status == "queued",
                        CodexEmbeddingJob.updated_at < cutoff,
                    )
                    .order_by(CodexEmbeddingJob.updated_at, CodexEmbeddingJob.project_id)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        recovered = 0
        failed = 0
        for job in jobs:
            job.dispatch_attempts += 1
            job.updated_at = datetime.now(UTC)
            try:
                result = celery_app.send_task(
                    "codex.backfill_embeddings",
                    args=(job.project_id, 32),
                )
                job.task_id = result.id
                job.error_code = None
                job.last_error = None
                recovered += 1
            except Exception as error:
                await record_dispatch_failure(
                    db,
                    job.project_id,
                    error=error,
                    max_attempts=MAX_DISPATCH_ATTEMPTS,
                )
                failed += 1
        await db.commit()
        return {"recovered": recovered, "failed": failed}


@shared_task(name="codex.recover_stale_embedding_jobs")
def recover_stale_embedding_jobs():
    return run_async(_recover_stale_embedding_jobs_async())


__all__ = [
    "RETRYABLE_ERRORS",
    "backfill_codex_embeddings_task",
    "backfill_chapter_chunks_task",
    "recover_stale_embedding_jobs",
]
