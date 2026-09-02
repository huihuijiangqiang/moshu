"""Codex embedding backfill with bounded retries and persistent dead letters."""
import asyncio
from datetime import UTC, datetime, timedelta

import httpx
from celery import shared_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config import settings
from db.models_embedding import CodexEmbeddingJob
from services.codex import count_stale_entries
from services.codex_embedding import embed_missing_codex_entries
from services.embedding import EmbeddingProviderError, GatewayEmbeddingProvider
from services.embedding_jobs import (
    begin_attempt,
    complete_job,
    fail_job,
    record_dispatch_failure,
)

engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

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
        try:
            embedded = await embed_missing_codex_entries(
                db,
                GatewayEmbeddingProvider(),
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
            raise
        return {
            "status": "success",
            "project_id": project_id,
            "embedded_count": embedded,
            "remaining_count": remaining,
        }


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
    "recover_stale_embedding_jobs",
]
