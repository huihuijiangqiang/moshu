"""
Consistency Celery tasks - ordered pipeline for each body revision
"""
import asyncio
from datetime import datetime, timezone

from celery import chain, shared_task
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config import settings
from db import ChapterBody, ConsistencyClaim, ConsistencyRun, DocumentSummary
from providers.consistency import ConsistencyProvider
from services.outbox import OutboxService
from services.rule_scanner import RuleScanner

# Create async engine for tasks
engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


def run_async(coro):
    """Helper to run async functions in sync Celery tasks"""
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(coro)


@shared_task(bind=True, name="consistency.process_body_saved")
def process_body_saved(self, payload: dict):
    """
    处理 chapter.body_saved 事件 - 启动有序管道
    """
    return run_async(_process_body_saved_async(self.request.id, payload))


async def _process_body_saved_async(task_id: str, payload: dict):
    async with AsyncSessionLocal() as db:
        project_id = payload["project_id"]
        chapter_id = payload["chapter_id"]
        body_rev = payload["body_rev"]
        # Normalize trigger to allowed value
        raw_trigger = payload.get("trigger", "user_edit")
        trigger = "body_save" if raw_trigger in ("user_edit", "manual", "autosave") else "body_save"

        # Check if run already exists for this chapter/body_rev/pipeline
        pipeline_version = "1.0.0"
        existing_run = await db.execute(
            select(ConsistencyRun).where(
                ConsistencyRun.chapter_id == chapter_id,
                ConsistencyRun.body_rev == body_rev,
                ConsistencyRun.pipeline_version == pipeline_version,
            )
        )
        run = existing_run.scalar_one_or_none()

        if not run:
            # Create consistency run
            run = ConsistencyRun(
                project_id=project_id,
                chapter_id=chapter_id,
                body_rev=body_rev,
                pipeline_version=pipeline_version,
                status="pending",
                trigger=trigger,
                started_at=datetime.now(timezone.utc),
            )
            db.add(run)
            await db.commit()
            await db.refresh(run)

        # Launch ordered pipeline: extract -> summarize -> scan
        # Use Celery chain for explicit ordering
        pipeline = chain(
            extract_claims.si(run.id),
            generate_summary.si(run.id),
            scan_rules.si(run.id),
        )
        pipeline.apply_async()

        return {
            "status": "dispatched",
            "task_id": task_id,
            "run_id": run.id,
            "payload": payload,
        }


@shared_task(bind=True, name="consistency.extract_claims")
def extract_claims(self, run_id: int):
    """
    Step 1: Extract structured claims from body revision
    """
    return run_async(_extract_claims_async(self.request.id, run_id))


async def _extract_claims_async(task_id: str, run_id: int):
    async with AsyncSessionLocal() as db:
        # Load run
        run_stmt = select(ConsistencyRun).where(ConsistencyRun.id == run_id)
        run_result = await db.execute(run_stmt)
        run = run_result.scalar_one_or_none()

        if not run:
            return {"status": "error", "message": "Run not found", "run_id": run_id}

        # Update status
        run.status = "extracting"
        await db.commit()

        # Load exact body revision
        body_stmt = select(ChapterBody).where(
            ChapterBody.chapter_id == run.chapter_id,
            ChapterBody.rev == run.body_rev,
        )
        body_result = await db.execute(body_stmt)
        body = body_result.scalar_one_or_none()

        if not body:
            run.status = "failed"
            run.error_code = "body_not_found"
            run.error_detail = f"Body at revision {run.body_rev} no longer exists"
            await db.commit()
            return {"status": "error", "message": "Body not found"}

        # Verify body revision hasn't changed
        if body.rev != run.body_rev:
            run.status = "failed"
            run.error_code = "stale_revision"
            run.error_detail = f"Body revision changed from {run.body_rev} to {body.rev}"
            await db.commit()
            return {"status": "error", "message": "Stale revision"}

        # Extract claims using provider
        provider = ConsistencyProvider()
        try:
            claims_data = await provider.extract_claims(
                content_html=body.content_html,
                project_id=run.project_id,
                chapter_id=run.chapter_id,
            )

            # Persist claims using actual ORM schema
            for claim_data in claims_data:
                claim = ConsistencyClaim(
                    project_id=run.project_id,
                    subject_text=claim_data["subject_text"],
                    predicate=claim_data["predicate"],
                    object_type=claim_data["object_type"],
                    object_value=claim_data.get("object_value"),
                    polarity=claim_data.get("polarity", "positive"),
                    certainty=claim_data.get("certainty", "explicit"),
                    source_kind="body",
                    chapter_id=run.chapter_id,
                    body_rev=run.body_rev,
                    paragraph_id=claim_data.get("paragraph_id"),
                    extractor_version=provider.extractor_version,
                    confidence=claim_data.get("confidence", 0.9),
                    fingerprint=claim_data["fingerprint"],
                    status="accepted",
                )
                db.add(claim)

            await db.commit()

            # Final revision check before marking complete
            body_check = await db.execute(body_stmt)
            body_final = body_check.scalar_one_or_none()
            if not body_final or body_final.rev != run.body_rev:
                run.status = "failed"
                run.error_code = "stale_revision"
                run.error_detail = "Body revision changed during extraction"
                await db.commit()
                return {"status": "error", "message": "Stale revision detected"}

            return {
                "status": "success",
                "task_id": task_id,
                "run_id": run_id,
                "claims_count": len(claims_data),
            }
        except Exception as e:
            run.status = "failed"
            run.error_code = "extraction_error"
            run.error_detail = str(e)
            await db.commit()
            return {"status": "error", "message": str(e), "run_id": run_id}


@shared_task(bind=True, name="consistency.generate_summary")
def generate_summary(self, run_id: int):
    """
    Step 2: Generate and persist document summary
    """
    return run_async(_generate_summary_async(self.request.id, run_id))


async def _generate_summary_async(task_id: str, run_id: int):
    async with AsyncSessionLocal() as db:
        # Load run
        run_stmt = select(ConsistencyRun).where(ConsistencyRun.id == run_id)
        run_result = await db.execute(run_stmt)
        run = run_result.scalar_one_or_none()

        if not run:
            return {"status": "error", "message": "Run not found", "run_id": run_id}

        run.status = "summarizing"
        await db.commit()

        # Load body
        body_stmt = select(ChapterBody).where(
            ChapterBody.chapter_id == run.chapter_id,
            ChapterBody.rev == run.body_rev,
        )
        body_result = await db.execute(body_stmt)
        body = body_result.scalar_one_or_none()

        if not body or body.rev != run.body_rev:
            run.status = "failed"
            run.error_code = "stale_revision"
            await db.commit()
            return {"status": "error", "message": "Stale revision"}

        # Generate summary
        provider = ConsistencyProvider()
        try:
            summary_text, token_count = await provider.generate_summary(
                content_html=body.content_html,
                summary_type="chapter",
            )

            summary_version = "1.0.0"

            # Mark older active summaries as superseded
            await db.execute(
                update(DocumentSummary)
                .where(
                    DocumentSummary.owner_type == "chapter",
                    DocumentSummary.owner_id == run.chapter_id,
                    DocumentSummary.status == "active",
                )
                .values(status="superseded")
            )

            # Persist new summary
            summary = DocumentSummary(
                owner_type="chapter",
                owner_id=run.chapter_id,
                source_rev=run.body_rev,
                summary_version=summary_version,
                content=summary_text,
                model_id="gpt-4o-mini",
                token_count=token_count,
                status="active",
            )
            db.add(summary)
            await db.commit()

            # Final revision check
            body_check = await db.execute(body_stmt)
            body_final = body_check.scalar_one_or_none()
            if not body_final or body_final.rev != run.body_rev:
                # Mark summary as stale
                summary.status = "stale"
                await db.commit()
                run.status = "failed"
                run.error_code = "stale_revision"
                await db.commit()
                return {"status": "error", "message": "Stale revision detected"}

            return {
                "status": "success",
                "task_id": task_id,
                "run_id": run_id,
                "summary_length": len(summary_text),
            }
        except Exception as e:
            run.status = "failed"
            run.error_code = "summary_error"
            run.error_detail = str(e)
            await db.commit()
            return {"status": "error", "message": str(e), "run_id": run_id}


@shared_task(bind=True, name="consistency.scan_rules")
def scan_rules(self, run_id: int):
    """
    Step 3: Execute rule scanning (observes newly persisted claims)
    """
    return run_async(_scan_rules_async(self.request.id, run_id))


async def _scan_rules_async(task_id: str, run_id: int):
    async with AsyncSessionLocal() as db:
        # Load run
        run_stmt = select(ConsistencyRun).where(ConsistencyRun.id == run_id)
        run_result = await db.execute(run_stmt)
        run = run_result.scalar_one_or_none()

        if not run:
            return {"status": "error", "message": "Run not found", "run_id": run_id}

        # Verify body revision
        body_stmt = select(ChapterBody).where(
            ChapterBody.chapter_id == run.chapter_id,
            ChapterBody.rev == run.body_rev,
        )
        body_result = await db.execute(body_stmt)
        body = body_result.scalar_one_or_none()

        if not body or body.rev != run.body_rev:
            run.status = "failed"
            run.error_code = "stale_revision"
            run.error_detail = f"Body revision changed from {run.body_rev}"
            await db.commit()
            return {"status": "error", "message": "Stale revision"}

        run.status = "scanning"
        await db.commit()

        # Run scanner
        scanner = RuleScanner(rule_version="1.0.0")
        try:
            issue_ids = await scanner.scan_chapter(
                db=db,
                run_id=run.id,
                project_id=run.project_id,
                chapter_id=run.chapter_id,
                body_rev=run.body_rev,
            )

            # Final revision check
            body_check = await db.execute(body_stmt)
            body_final = body_check.scalar_one_or_none()
            if not body_final or body_final.rev != run.body_rev:
                run.status = "failed"
                run.error_code = "stale_revision"
                run.error_detail = "Body changed during scan"
                await db.commit()
                return {"status": "error", "message": "Stale revision detected"}

            run.status = "completed"
            run.finished_at = datetime.now(timezone.utc)
            await db.commit()

            return {
                "status": "success",
                "task_id": task_id,
                "run_id": run_id,
                "issues_found": len(issue_ids),
            }
        except Exception as e:
            run.status = "failed"
            run.error_code = "scan_error"
            run.error_detail = str(e)
            await db.commit()
            return {"status": "error", "message": str(e), "run_id": run_id}


@shared_task(bind=True, name="consistency.dispatch_outbox")
def dispatch_outbox(self, batch_size: int = 10):
    """
    从 outbox 分发事件到对应的 Celery 任务
    """
    return run_async(_dispatch_outbox_async(self.request.id, batch_size))


async def _dispatch_outbox_async(task_id: str, batch_size: int):
    async with AsyncSessionLocal() as db:
        # Lease batch - need owner_id
        owner_id = f"worker_{task_id}"
        events = await OutboxService.lease_batch(
            db, owner_id=owner_id, batch_size=batch_size, lease_duration_seconds=300
        )

        dispatched = 0
        failed = 0

        for event in events:
            try:
                # Route based on topic
                if event.topic == "chapter.body_saved":
                    process_body_saved.delay(event.payload)
                    await OutboxService.mark_sent(db, event.id, event.lease_token)
                    dispatched += 1
                else:
                    # Unknown topic
                    await OutboxService.mark_failed(
                        db, event.id, event.lease_token, error_message=f"Unknown topic: {event.topic}"
                    )
                    failed += 1
            except Exception as e:
                await OutboxService.mark_failed(db, event.id, event.lease_token, error_message=str(e))
                failed += 1

        await db.commit()

        return {
            "status": "success",
            "task_id": task_id,
            "dispatched": dispatched,
            "failed": failed,
            "batch_size": batch_size,
        }
