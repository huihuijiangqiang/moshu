"""Durable image generation and recovery for comic-drama shots."""

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from celery_app import celery_app
from db import Adaptation, ProductionAsset, ProductionJob, Shot
from db.models_core import User
from db.models_usage import UsageLog
from db.session import AsyncSessionLocal
from providers.production_images import ImageGatewayError, generate_storyboard_image
from services.production_assets import IMAGE_EXTENSIONS, asset_path
from services.usage import UsageReservation, release_reservation, settle_fixed_credits
from tasks.generation import run_async

logger = logging.getLogger(__name__)
STALE_IMAGE_JOB_SECONDS = 20 * 60


def reservation_valid(log: UsageLog | None, now: datetime) -> bool:
    if log is None or log.status != "reserved" or log.reservation_expires_at is None:
        return False
    expires_at = log.reservation_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at > now


async def process_image_job(db: AsyncSession, job_id: str) -> str:
    job = await db.scalar(select(ProductionJob).where(ProductionJob.id == job_id).with_for_update(skip_locked=True))
    if job is None or job.status != "queued":
        await db.rollback()
        return "not_queued"
    await db.scalar(select(User).where(User.id == job.user_id).with_for_update())
    log = await db.scalar(select(UsageLog).where(UsageLog.id == job.usage_log_id).with_for_update())
    if not reservation_valid(log, datetime.now(UTC)):
        job.status = "failed"
        job.error_code = "image_reservation_expired"
        job.finished_at = datetime.now(UTC)
        if log is not None and log.status == "reserved":
            await release_reservation(
                db, UsageReservation(job.usage_log_id, job.credits), reason="image_reservation_expired",
            )
        await db.commit()
        return "reservation_expired"
    job.status = "running"
    job.started_at = datetime.now(UTC)
    prompt, model, adaptation_id = job.prompt, job.model, job.adaptation_id
    await db.commit()

    target = None
    try:
        adaptation = await db.get(Adaptation, adaptation_id)
        if adaptation is None:
            raise ImageGatewayError("image_adaptation_missing")
        image = await generate_storyboard_image(prompt, aspect_ratio=adaptation.aspect_ratio, model=model)
        job = await db.scalar(select(ProductionJob).where(ProductionJob.id == job_id).with_for_update())
        if job is None or job.status != "running":
            await db.rollback()
            return "no_longer_running"
        await db.scalar(select(User).where(User.id == job.user_id).with_for_update())
        log = await db.scalar(select(UsageLog).where(UsageLog.id == job.usage_log_id).with_for_update())
        if not reservation_valid(log, datetime.now(UTC)):
            raise ImageGatewayError("image_reservation_expired")
        extension = IMAGE_EXTENSIONS[image.mime_type]
        asset_id = f"asset_{uuid4().hex[:24]}"
        storage_key = f"{job.adaptation_id}/{asset_id}.{extension}"
        target = asset_path(storage_key)

        def write_image() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(image.data)

        await asyncio.to_thread(write_image)
        asset = ProductionAsset(
            id=asset_id, adaptation_id=job.adaptation_id, episode_id=job.episode_id,
            shot_id=job.shot_id, kind="image", original_filename=f"{asset_id}.{extension}",
            storage_key=storage_key, mime_type=image.mime_type, byte_size=len(image.data),
            width=image.width, height=image.height, sha256=hashlib.sha256(image.data).hexdigest(),
            status="draft", created_by=job.user_id,
            metadata_json={"source": "image_generation", "job_id": job.id, "provider_id": image.provider_id},
        )
        db.add(asset)
        if job.shot_id is not None:
            shot = await db.get(Shot, job.shot_id)
            if shot is not None:
                shot.reference_asset_ids = [*dict.fromkeys([*(shot.reference_asset_ids or []), asset_id])]
        await settle_fixed_credits(db, UsageReservation(job.usage_log_id, job.credits))
        job.asset_id = asset_id
        job.status = "completed"
        job.finished_at = datetime.now(UTC)
        await db.commit()
        return "completed"
    except Exception as error:
        await db.rollback()
        if target is not None:
            await asyncio.to_thread(target.unlink, missing_ok=True)
        code = error.code if isinstance(error, ImageGatewayError) else "image_generation_failed"
        logger.warning("Image job %s failed: %s", job_id, code)
        job = await db.scalar(select(ProductionJob).where(ProductionJob.id == job_id).with_for_update())
        if job is not None and job.status == "running":
            await db.scalar(select(User).where(User.id == job.user_id).with_for_update())
            job.status = "failed"
            job.error_code = code
            job.finished_at = datetime.now(UTC)
            if job.usage_log_id is not None:
                await release_reservation(
                    db, UsageReservation(job.usage_log_id, job.credits), reason=code,
                )
            await db.commit()
        else:
            await db.rollback()
        return "failed"


async def queued_image_job_ids(db: AsyncSession, batch_size: int = 20) -> list[str]:
    rows = await db.execute(
        select(ProductionJob.id).where(ProductionJob.status == "queued")
        .order_by(ProductionJob.created_at).limit(batch_size)
    )
    return list(rows.scalars().all())


async def recover_stale_image_jobs(
    db: AsyncSession, *, now: datetime | None = None, stale_seconds: int = STALE_IMAGE_JOB_SECONDS,
) -> int:
    current = now or datetime.now(UTC)
    rows = (await db.execute(
        select(ProductionJob).where(
            ProductionJob.status == "running",
            ProductionJob.started_at < current - timedelta(seconds=stale_seconds),
        ).with_for_update(skip_locked=True)
    )).scalars().all()
    for job in rows:
        await db.scalar(select(User).where(User.id == job.user_id).with_for_update())
        job.status = "failed"
        job.error_code = "image_job_timeout"
        job.finished_at = current
        if job.usage_log_id is not None:
            await release_reservation(
                db, UsageReservation(job.usage_log_id, job.credits), reason="image_job_timeout",
            )
    await db.commit()
    return len(rows)


@shared_task(name="production.generate_image")
def generate_image_task(job_id: str):
    async def run():
        async with AsyncSessionLocal() as db:
            return await process_image_job(db, job_id)

    return run_async(run())


@shared_task(name="production.dispatch_queued")
def dispatch_queued_image_jobs_task():
    async def load():
        async with AsyncSessionLocal() as db:
            return await queued_image_job_ids(db)

    ids = run_async(load())
    for job_id in ids:
        celery_app.send_task("production.generate_image", args=[job_id], retry=False)
    return len(ids)


@shared_task(name="production.recover_stale")
def recover_stale_image_jobs_task():
    async def run():
        async with AsyncSessionLocal() as db:
            return await recover_stale_image_jobs(db)

    return run_async(run())
