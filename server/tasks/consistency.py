"""
Consistency Celery tasks - ordered pipeline for each body revision

关键设计（都是修过的缺陷）：

* 读的是不可变快照 ChapterVersion(chapter_id, rev)，不是可变的 ChapterBody。
  ChapterBody 每行只保存最新版本，run 排队期间作者再存一次，任务就会拿到新
  正文却按旧 body_rev 记账 —— 结论对不上任何一版内容。
* 失败必须抛异常。早期实现返回 {"status": "error"}，Celery 视为成功，chain 会
  继续跑后续步骤，于是「抽取失败」后照样出摘要和扫描结论。
* 新版本发布时旧版本的 claim 要在同一事务里原子作废，避免新旧 claim 共存导致
  规则误报。
* 摘要的「当前有效版本」切换必须原子：先条件性 supersede，再插入新 active 行，
  最后校验头版本仍是自己。
"""
import asyncio
from datetime import datetime, timezone

from celery import chain, shared_task
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config import settings
from db import ChapterVersion, ConsistencyClaim, ConsistencyRun, DocumentSummary
from providers.consistency import ConsistencyProvider
from services.consistency import (
    EXTRACTOR_VERSION,
    PIPELINE_VERSION,
    RULE_VERSION,
    SUMMARY_VERSION,
    normalize_run_trigger,
)
from services.outbox import OutboxService
from services.rule_scanner import RuleScanner

# Create async engine for tasks
engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class RunNotFoundError(RuntimeError):
    """run 记录不存在 —— 上游传错了 run_id，必须让任务失败。"""


class StaleRevisionError(RuntimeError):
    """正文版本已经不是 run 对应的那一版；后续步骤不能继续。"""


class BodyRevisionMissingError(RuntimeError):
    """找不到 run 对应的不可变正文快照。"""


def run_async(coro):
    """Helper to run async functions in sync Celery tasks"""
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(coro)


async def load_run(db, run_id: int) -> ConsistencyRun:
    """加载 run；不存在直接抛错，让 chain 中断。"""
    result = await db.execute(select(ConsistencyRun).where(ConsistencyRun.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise RunNotFoundError(f"consistency run {run_id} not found")
    return run


async def load_body_snapshot(db, run: ConsistencyRun) -> ChapterVersion:
    """读取 run 对应的不可变正文快照。

    ChapterVersion 是按 (chapter_id, rev) 保存的历史快照，内容不会被后续保存
    覆盖，因此同一个 run 无论何时执行都看到同一份文本。
    """
    result = await db.execute(
        select(ChapterVersion)
        .where(ChapterVersion.chapter_id == run.chapter_id)
        .where(ChapterVersion.rev == run.body_rev)
        .order_by(ChapterVersion.id.desc())
        .limit(1)
    )
    snapshot = result.scalar_one_or_none()
    if not snapshot:
        raise BodyRevisionMissingError(
            f"chapter {run.chapter_id} has no immutable snapshot at rev {run.body_rev}"
        )
    return snapshot


async def ensure_run_is_current(db, run: ConsistencyRun) -> None:
    """确认没有更新的正文版本；有则说明本 run 的结论已经过时。"""
    result = await db.execute(
        select(ChapterVersion.rev)
        .where(ChapterVersion.chapter_id == run.chapter_id)
        .order_by(ChapterVersion.rev.desc())
        .limit(1)
    )
    latest_rev = result.scalar_one_or_none()
    if latest_rev is not None and latest_rev > run.body_rev:
        raise StaleRevisionError(
            f"chapter {run.chapter_id} advanced from rev {run.body_rev} to {latest_rev}"
        )


async def fail_run(db, run_id: int, *, error_code: str, error_detail: str) -> None:
    """把 run 标记为 failed（独立 UPDATE，避免复用可能已回滚的 ORM 状态）。"""
    await db.execute(
        update(ConsistencyRun)
        .where(ConsistencyRun.id == run_id)
        .values(
            status="failed",
            error_code=error_code,
            error_detail=error_detail[:2000],
            finished_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()


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
        # 映射到 ck_consistency_run_trigger 允许的取值；已合法的值原样保留
        trigger = normalize_run_trigger(payload.get("trigger"))

        # Check if run already exists for this chapter/body_rev/pipeline
        existing_run = await db.execute(
            select(ConsistencyRun).where(
                ConsistencyRun.chapter_id == chapter_id,
                ConsistencyRun.body_rev == body_rev,
                ConsistencyRun.pipeline_version == PIPELINE_VERSION,
            )
        )
        run = existing_run.scalar_one_or_none()

        if not run:
            # Create consistency run
            run = ConsistencyRun(
                project_id=project_id,
                chapter_id=chapter_id,
                body_rev=body_rev,
                pipeline_version=PIPELINE_VERSION,
                status="pending",
                trigger=trigger,
                started_at=datetime.now(timezone.utc),
            )
            db.add(run)
            await db.commit()
            await db.refresh(run)

        # Launch ordered pipeline: extract -> summarize -> scan
        # 用 chain 显式串行；任一步抛异常，后续步骤不会执行
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
        run = await load_run(db, run_id)

        try:
            snapshot = await load_body_snapshot(db, run)
            await ensure_run_is_current(db, run)
        except (BodyRevisionMissingError, StaleRevisionError) as exc:
            code = (
                "body_not_found"
                if isinstance(exc, BodyRevisionMissingError)
                else "stale_revision"
            )
            await fail_run(db, run_id, error_code=code, error_detail=str(exc))
            raise

        run.status = "extracting"
        await db.commit()

        provider = ConsistencyProvider()
        try:
            claims_data = await provider.extract_claims(
                content_html=snapshot.content_html,
                project_id=run.project_id,
                chapter_id=run.chapter_id,
            )

            # 新版本发布：同章旧版本的 claim 在同一事务里作废，避免新旧共存误报
            superseded = await db.execute(
                update(ConsistencyClaim)
                .where(
                    ConsistencyClaim.chapter_id == run.chapter_id,
                    ConsistencyClaim.source_kind == "body",
                    ConsistencyClaim.body_rev < run.body_rev,
                    ConsistencyClaim.status.in_(["candidate", "accepted"]),
                )
                .values(status="superseded", updated_at=datetime.now(timezone.utc))
            )

            # 本版本重跑时先清掉上一次的结果，保证幂等（唯一键否则会冲突）
            await db.execute(
                update(ConsistencyClaim)
                .where(
                    ConsistencyClaim.chapter_id == run.chapter_id,
                    ConsistencyClaim.source_kind == "body",
                    ConsistencyClaim.body_rev == run.body_rev,
                    ConsistencyClaim.extractor_version == EXTRACTOR_VERSION,
                    ConsistencyClaim.status.in_(["candidate", "accepted"]),
                )
                .values(status="superseded", updated_at=datetime.now(timezone.utc))
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
                    extractor_version=EXTRACTOR_VERSION,
                    confidence=claim_data.get("confidence", 0.9),
                    fingerprint=claim_data["fingerprint"],
                    status="accepted",
                )
                db.add(claim)

            # 提交前再确认版本没变：确认通过后 claim 与 supersede 一起落库
            await ensure_run_is_current(db, run)
            await db.commit()

            return {
                "status": "success",
                "task_id": task_id,
                "run_id": run_id,
                "claims_count": len(claims_data),
                "superseded_claims": superseded.rowcount,
            }
        except StaleRevisionError as exc:
            await db.rollback()
            await fail_run(db, run_id, error_code="stale_revision", error_detail=str(exc))
            raise
        except Exception as exc:
            await db.rollback()
            await fail_run(db, run_id, error_code="extraction_error", error_detail=str(exc))
            raise


@shared_task(bind=True, name="consistency.generate_summary")
def generate_summary(self, run_id: int):
    """
    Step 2: Generate and persist document summary
    """
    return run_async(_generate_summary_async(self.request.id, run_id))


async def _generate_summary_async(task_id: str, run_id: int):
    async with AsyncSessionLocal() as db:
        run = await load_run(db, run_id)

        try:
            snapshot = await load_body_snapshot(db, run)
            await ensure_run_is_current(db, run)
        except (BodyRevisionMissingError, StaleRevisionError) as exc:
            code = (
                "body_not_found"
                if isinstance(exc, BodyRevisionMissingError)
                else "stale_revision"
            )
            await fail_run(db, run_id, error_code=code, error_detail=str(exc))
            raise

        run.status = "summarizing"
        await db.commit()

        provider = ConsistencyProvider()
        try:
            summary_text, token_count = await provider.generate_summary(
                content_html=snapshot.content_html,
                summary_type="chapter",
            )

            # 原子切换有效摘要：只 supersede 比本版本旧的 active 行，
            # 更新的版本（并发下已经写入）不能被打回，否则会退回旧摘要。
            await db.execute(
                update(DocumentSummary)
                .where(
                    DocumentSummary.owner_type == "chapter",
                    DocumentSummary.owner_id == run.chapter_id,
                    DocumentSummary.status == "active",
                    DocumentSummary.source_rev < run.body_rev,
                )
                .values(status="superseded", updated_at=datetime.now(timezone.utc))
            )

            # 本版本重跑：同 (owner, source_rev, summary_version) 已存在则原地更新，
            # 否则会撞 uq_document_summary_key。
            existing = (
                await db.execute(
                    select(DocumentSummary).where(
                        DocumentSummary.owner_type == "chapter",
                        DocumentSummary.owner_id == run.chapter_id,
                        DocumentSummary.source_rev == run.body_rev,
                        DocumentSummary.summary_version == SUMMARY_VERSION,
                    )
                )
            ).scalar_one_or_none()

            if existing:
                existing.content = summary_text
                existing.model_id = settings.consistency_summary_model
                existing.token_count = token_count
                existing.status = "active"
                existing.updated_at = datetime.now(timezone.utc)
            else:
                db.add(
                    DocumentSummary(
                        owner_type="chapter",
                        owner_id=run.chapter_id,
                        source_rev=run.body_rev,
                        summary_version=SUMMARY_VERSION,
                        content=summary_text,
                        model_id=settings.consistency_summary_model,
                        token_count=token_count,
                        status="active",
                    )
                )
            await db.flush()

            # 头版本校验：并发下若已有更新版本的 active 摘要，本次结果应作废而
            # 不是覆盖它。
            head_rev = (
                await db.execute(
                    select(DocumentSummary.source_rev)
                    .where(
                        DocumentSummary.owner_type == "chapter",
                        DocumentSummary.owner_id == run.chapter_id,
                        DocumentSummary.status == "active",
                    )
                    .order_by(DocumentSummary.source_rev.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            if head_rev is not None and head_rev > run.body_rev:
                await db.execute(
                    update(DocumentSummary)
                    .where(
                        DocumentSummary.owner_type == "chapter",
                        DocumentSummary.owner_id == run.chapter_id,
                        DocumentSummary.source_rev == run.body_rev,
                        DocumentSummary.summary_version == SUMMARY_VERSION,
                    )
                    .values(status="stale", updated_at=datetime.now(timezone.utc))
                )
                await db.commit()
                raise StaleRevisionError(
                    f"chapter {run.chapter_id} already has an active summary at rev {head_rev}"
                )

            await ensure_run_is_current(db, run)
            await db.commit()

            return {
                "status": "success",
                "task_id": task_id,
                "run_id": run_id,
                "summary_length": len(summary_text),
            }
        except StaleRevisionError as exc:
            await fail_run(db, run_id, error_code="stale_revision", error_detail=str(exc))
            raise
        except Exception as exc:
            await db.rollback()
            await fail_run(db, run_id, error_code="summary_error", error_detail=str(exc))
            raise


@shared_task(bind=True, name="consistency.scan_rules")
def scan_rules(self, run_id: int):
    """
    Step 3: Execute rule scanning (observes newly persisted claims)
    """
    return run_async(_scan_rules_async(self.request.id, run_id))


async def _scan_rules_async(task_id: str, run_id: int):
    async with AsyncSessionLocal() as db:
        run = await load_run(db, run_id)

        try:
            await load_body_snapshot(db, run)
            await ensure_run_is_current(db, run)
        except (BodyRevisionMissingError, StaleRevisionError) as exc:
            code = (
                "body_not_found"
                if isinstance(exc, BodyRevisionMissingError)
                else "stale_revision"
            )
            await fail_run(db, run_id, error_code=code, error_detail=str(exc))
            raise

        run.status = "scanning"
        await db.commit()

        scanner = RuleScanner(rule_version=RULE_VERSION)
        try:
            issue_ids = await scanner.scan_chapter(
                db=db,
                run_id=run.id,
                project_id=run.project_id,
                chapter_id=run.chapter_id,
                body_rev=run.body_rev,
            )

            # 提交前确认版本仍是本 run 的那一版；否则结论不应落库
            await ensure_run_is_current(db, run)

            run.status = "completed"
            run.finished_at = datetime.now(timezone.utc)
            await db.commit()

            return {
                "status": "success",
                "task_id": task_id,
                "run_id": run_id,
                "issues_found": len(issue_ids),
            }
        except StaleRevisionError as exc:
            await db.rollback()
            await fail_run(db, run_id, error_code="stale_revision", error_detail=str(exc))
            raise
        except Exception as exc:
            await db.rollback()
            await fail_run(db, run_id, error_code="scan_error", error_detail=str(exc))
            raise


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


__all__ = [
    "BodyRevisionMissingError",
    "EXTRACTOR_VERSION",
    "RunNotFoundError",
    "StaleRevisionError",
    "dispatch_outbox",
    "extract_claims",
    "generate_summary",
    "process_body_saved",
    "scan_rules",
]
