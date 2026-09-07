"""State transitions for observable Codex embedding backfills."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_embedding import CodexEmbeddingJob


async def lock_job(db: AsyncSession, project_id: str) -> CodexEmbeddingJob | None:
    return (
        await db.execute(
            select(CodexEmbeddingJob)
            .where(CodexEmbeddingJob.project_id == project_id)
            .with_for_update()
        )
    ).scalar_one_or_none()


async def queue_job(db: AsyncSession, project_id: str, remaining_count: int) -> CodexEmbeddingJob:
    job = await lock_job(db, project_id)
    if job is None:
        job = CodexEmbeddingJob(
            project_id=project_id,
            status="queued",
            attempts=0,
            dispatch_attempts=0,
            embedded_count=0,
            remaining_count=0,
        )
        db.add(job)
    job.status = "queued"
    job.attempts = 0
    job.dispatch_attempts = 0
    job.embedded_count = 0
    job.remaining_count = remaining_count
    job.task_id = None
    job.error_code = None
    job.last_error = None
    job.started_at = None
    job.last_attempt_at = None
    job.completed_at = None
    job.exhausted_at = None
    await db.flush()
    return job


async def begin_attempt(
    db: AsyncSession,
    project_id: str,
    *,
    attempt: int,
    task_id: str | None,
) -> CodexEmbeddingJob:
    now = datetime.now(UTC)
    job = await lock_job(db, project_id)
    if job is None:
        job = CodexEmbeddingJob(
            project_id=project_id,
            status="running",
            attempts=0,
            embedded_count=0,
            remaining_count=0,
        )
        db.add(job)
    job.status = "running"
    job.attempts = max(job.attempts, attempt)
    job.task_id = task_id or job.task_id
    job.started_at = job.started_at or now
    job.last_attempt_at = now
    await db.flush()
    return job


async def complete_job(
    db: AsyncSession,
    project_id: str,
    *,
    embedded_count: int,
    remaining_count: int,
) -> CodexEmbeddingJob:
    job = await lock_job(db, project_id)
    if job is None:
        job = CodexEmbeddingJob(
            project_id=project_id,
            status="succeeded",
            attempts=0,
            embedded_count=0,
            remaining_count=0,
        )
        db.add(job)
    job.status = "succeeded"
    job.embedded_count += embedded_count
    job.remaining_count = remaining_count
    job.error_code = None
    job.last_error = None
    job.completed_at = datetime.now(UTC)
    job.exhausted_at = None
    await db.flush()
    return job


async def fail_job(
    db: AsyncSession,
    project_id: str,
    *,
    attempt: int,
    error: Exception,
    remaining_count: int,
    exhausted: bool,
) -> CodexEmbeddingJob:
    job = await lock_job(db, project_id)
    if job is None:
        job = CodexEmbeddingJob(
            project_id=project_id,
            status="dead_letter" if exhausted else "retrying",
            attempts=0,
            embedded_count=0,
            remaining_count=0,
        )
        db.add(job)
    now = datetime.now(UTC)
    job.status = "dead_letter" if exhausted else "retrying"
    job.attempts = max(job.attempts, attempt)
    job.remaining_count = remaining_count
    job.error_code = type(error).__name__[:100]
    job.last_error = str(error).replace("\r", " ").replace("\n", " ")[:2000] or "Embedding failed"
    job.last_attempt_at = now
    job.exhausted_at = now if exhausted else None
    await db.flush()
    return job


async def record_dispatch_failure(
    db: AsyncSession,
    project_id: str,
    *,
    error: Exception,
    max_attempts: int,
) -> CodexEmbeddingJob | None:
    """Record broker recovery failures without colliding with task retries."""
    job = await lock_job(db, project_id)
    if job is None or job.status != "queued":
        return job

    now = datetime.now(UTC)
    job.error_code = type(error).__name__[:100]
    job.last_error = (
        str(error).replace("\r", " ").replace("\n", " ")[:2000]
        or "Dispatch failed"
    )
    job.last_attempt_at = now
    job.updated_at = now
    if job.dispatch_attempts >= max_attempts:
        job.status = "dead_letter"
        job.exhausted_at = now
    await db.flush()
    return job
