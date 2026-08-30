"""
Consistency Celery tasks - extraction, summarization, scanning
"""
import asyncio

from celery import shared_task
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config import settings
from db import ChapterBody, ConsistencyClaim, ConsistencyRun, DocumentSummary, OutboxEvent
from providers.llm import EmbeddingProvider, StructuredExtractionProvider
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
    处理 chapter.body_saved 事件

    Args:
        payload: {
            "project_id": str,
            "chapter_id": str,
            "body_rev": int,
            "content_hash": str,
            "trigger": str
        }
    """
    return run_async(_process_body_saved_async(self.request.id, payload))


async def _process_body_saved_async(task_id: str, payload: dict):
    async with AsyncSessionLocal() as db:
        project_id = payload["project_id"]
        chapter_id = payload["chapter_id"]
        body_rev = payload["body_rev"]

        # Create consistency run
        run = ConsistencyRun(
            project_id=project_id,
            chapter_id=chapter_id,
            body_rev=body_rev,
            status="pending",
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)

        # Enqueue extraction, summary, and scanning tasks
        extract_claims.delay(run.id)
        generate_summary.delay(run.id)
        scan_rules.delay(run.id)

        return {
            "status": "dispatched",
            "task_id": task_id,
            "run_id": run.id,
            "payload": payload,
        }


@shared_task(bind=True, name="consistency.extract_claims")
def extract_claims(self, run_id: int):
    """
    从章节正文提取结构化 claims
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

        # Load chapter body
        body_stmt = select(ChapterBody).where(
            ChapterBody.chapter_id == run.chapter_id,
            ChapterBody.rev == run.body_rev,
        )
        body_result = await db.execute(body_stmt)
        body = body_result.scalar_one_or_none()

        if not body:
            run.status = "failed"
            run.error_message = "Chapter body not found"
            await db.commit()
            return {"status": "error", "message": "Chapter body not found"}

        # Extract claims using LLM
        extractor = StructuredExtractionProvider()
        try:
            claims_data = await extractor.extract_claims(body.content_html)

            # Generate embeddings
            embedding_provider = EmbeddingProvider()

            # Save claims
            for claim_data in claims_data:
                embedding = await embedding_provider.embed(claim_data["text"])

                claim = ConsistencyClaim(
                    chapter_id=run.chapter_id,
                    body_rev=run.body_rev,
                    source_kind="body",
                    claim_type=claim_data["type"],
                    text=claim_data["text"],
                    structured_data=claim_data.get("structured", {}),
                    confidence=claim_data.get("confidence", 0.9),
                    extractor_version=extractor.version,
                    fingerprint=claim_data["fingerprint"],
                    embedding=embedding,
                    status="accepted",
                )
                db.add(claim)

            await db.commit()

            run.status = "extracted"
            await db.commit()

            return {
                "status": "success",
                "task_id": task_id,
                "run_id": run_id,
                "claims_count": len(claims_data),
            }
        except Exception as e:
            run.status = "failed"
            run.error_message = str(e)
            await db.commit()
            return {"status": "error", "message": str(e), "run_id": run_id}


@shared_task(bind=True, name="consistency.generate_summary")
def generate_summary(self, run_id: int):
    """
    生成章节摘要
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

        # Load chapter body
        body_stmt = select(ChapterBody).where(
            ChapterBody.chapter_id == run.chapter_id,
            ChapterBody.rev == run.body_rev,
        )
        body_result = await db.execute(body_stmt)
        body = body_result.scalar_one_or_none()

        if not body:
            return {"status": "error", "message": "Chapter body not found"}

        # Generate summary using LLM
        extractor = StructuredExtractionProvider()
        try:
            summary_text = await extractor.generate_summary(body.content_html)

            # Generate embedding
            embedding_provider = EmbeddingProvider()
            embedding = await embedding_provider.embed(summary_text)

            # Save summary
            summary = DocumentSummary(
                document_type="chapter",
                document_id=run.chapter_id,
                content_rev=run.body_rev,
                summary_text=summary_text,
                embedding=embedding,
                model_name=extractor.model_name,
            )
            db.add(summary)
            await db.commit()

            return {
                "status": "success",
                "task_id": task_id,
                "run_id": run_id,
                "summary_length": len(summary_text),
            }
        except Exception as e:
            return {"status": "error", "message": str(e), "run_id": run_id}


@shared_task(bind=True, name="consistency.scan_rules")
def scan_rules(self, run_id: int):
    """
    执行三大确定性规则扫描
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

        # Update status
        run.status = "scanning"
        await db.commit()

        # Run scanner
        scanner = RuleScanner(rule_version="1.0.0")
        try:
            issues = await scanner.scan_chapter(
                db=db,
                run_id=run.id,
                project_id=run.project_id,
                chapter_id=run.chapter_id,
                body_rev=run.body_rev,
            )

            run.status = "completed"
            run.issues_found = len(issues)
            await db.commit()

            return {
                "status": "success",
                "task_id": task_id,
                "run_id": run_id,
                "issues_found": len(issues),
            }
        except Exception as e:
            run.status = "failed"
            run.error_message = str(e)
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
        service = OutboxService(db)

        # Lease batch
        events = await service.lease_batch(batch_size=batch_size, lease_duration_sec=300)

        dispatched = 0
        failed = 0

        for event in events:
            try:
                # Route based on topic
                if event.topic == "chapter.body_saved":
                    process_body_saved.delay(event.payload)
                    await service.mark_sent(event.id)
                    dispatched += 1
                else:
                    # Unknown topic
                    await service.mark_failed(event.id, error_message=f"Unknown topic: {event.topic}")
                    failed += 1
            except Exception as e:
                await service.mark_failed(event.id, error_message=str(e))
                failed += 1

        return {
            "status": "success",
            "task_id": task_id,
            "dispatched": dispatched,
            "failed": failed,
            "batch_size": batch_size,
        }
