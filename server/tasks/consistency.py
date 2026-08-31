"""
Consistency Celery tasks - per-revision pipeline

关键设计（每一条都是修过的缺陷）：

* 读的是不可变快照 ChapterVersion(chapter_id, rev)，不是可变的 ChapterBody。
  ChapterBody 每章只有一行、随保存被覆盖；run 排队期间作者再存一次，任务就会拿到
  新正文却按旧 body_rev 记账 —— 结论对不上任何一版内容。
* 提交前锁住 ChapterBody 头行再校验版本。只查 max(rev) 不够：查完到提交之间仍有
  窗口，新版本的 worker 可能在这段时间里写完，旧 worker 随后把过期结论盖上去。
  FOR UPDATE 把同一章的写入串行化，旧 worker 拿到锁时一定能看到新版本号。
* 失败必须抛异常。早期实现返回 {"status": "error"}，Celery 视为成功，chain 会继续
  跑后续步骤，于是「抽取失败」后照样出摘要和扫描结论。
* run 一旦 failed，后续步骤必须拒绝执行，否则失败的抽取之后仍会产出摘要与告警。
* 同一版本重跑跳过已有指纹，**不删除**任何 claim 行。架构 4.3 要求「旧正文版本的
  claim 不删除，标为 superseded，确保告警可追溯」；而且
  GuardIssueEvidence.claim_id 是 ON DELETE SET NULL 的外键 —— 删 claim 会把已有
  告警的证据指针悄悄清空。唯一键本就是 (chapter_id, body_rev, fingerprint,
  extractor_version)，同指纹重放就是同一行，跳过即幂等。
* 摘要与扫描在抽取之后并行投递：扫描只依赖已落库的 claim，等摘要（一次或多次
  模型调用）纯属浪费，告警会被拖慢几十秒。
* story_order 只由 services.timeline 依据**已确认的全局锚点**分配，模型给的顺序值
  一律被覆盖。**绝不用 Chapter.idx 推导 story_order**，也不采信模型的块内序号 ——
  架构 4.3 明确 story_order 是故事世界事件顺序，不是「第几章」；而抽取逐块进行，
  块内序号跨块没有共同标尺，被全项目范围的规则拿去排序就是另一种伪造。没有共同
  锚点时保持 NULL，claim 进待确认列表，不参与依赖时序的硬规则。

关于 run.status：摘要与扫描并行，单个 status 列无法同时表达两条支线。约定
status 跟踪扫描支线（告警是作者直接消费的产物），completed 由扫描步骤写入；
摘要是否就绪单独查 document_summaries。任一支线失败都会把 status 覆盖为 failed。
"""
import asyncio
from datetime import datetime, timezone
from typing import Optional

from celery import chain, group, shared_task
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config import settings
from db import ChapterBody, ChapterVersion, ConsistencyClaim, ConsistencyRun, DocumentSummary
from providers.consistency import ConsistencyProvider
from services.consistency import (
    EXTRACTOR_VERSION,
    PIPELINE_VERSION,
    RULE_VERSION,
    SUMMARY_VERSION,
    assign_narrative_positions,
    get_or_create_run,
    normalize_run_trigger,
    upsert_active_summary,
)
from services.embedding import GatewayEmbeddingProvider
from services.entity_linking import EntityLinker
from services.outbox import OutboxService
from services.retrieval import ConsistencyRetrieval
from services.rule_scanner import RuleScanner
from services.timeline import assign_story_orders

# Create async engine for tasks
engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class RunNotFoundError(RuntimeError):
    """run 记录不存在 —— 上游传错了 run_id，必须让任务失败。"""


class StaleRevisionError(RuntimeError):
    """正文版本已经不是 run 对应的那一版；结论不能落库。"""


class BodyRevisionMissingError(RuntimeError):
    """找不到 run 对应的不可变正文快照。"""


class RunFailedError(RuntimeError):
    """run 已经失败；下游步骤不得继续产出结论。"""


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


async def ensure_run_not_failed(db, run_id: int) -> None:
    """确认 run 没有被判定失败。

    读的是数据库当前值而不是内存里的 ORM 属性：并行支线可能刚刚把它标成 failed。
    """
    status = (
        await db.execute(select(ConsistencyRun.status).where(ConsistencyRun.id == run_id))
    ).scalar_one_or_none()
    if status == "failed":
        raise RunFailedError(f"consistency run {run_id} already failed; refusing to continue")


async def load_body_snapshot(db, run: ConsistencyRun) -> ChapterVersion:
    """读取 run 对应的不可变正文快照。

    ChapterVersion 按 (chapter_id, rev) 保存历史快照，内容不会被后续保存覆盖，
    因此同一个 run 无论何时执行都看到同一份文本。
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


async def lock_body_head(db, chapter_id: str) -> Optional[ChapterBody]:
    """锁住章节正文头行（SELECT ... FOR UPDATE）。

    ChapterBody 是每章唯一的可变行，也是 rev 乐观锁的载体。锁住它可以把「校验版本
    → 提交结论」变成对同一章串行的临界区：新版本的写入方要么在旧 worker 之前完成
    （旧 worker 拿锁后看到更大的 rev 而放弃），要么等旧 worker 提交后再写。

    SQLite 不支持行级锁，FOR UPDATE 会被忽略 —— 单元测试仍能验证版本比较逻辑，
    真正的并发互斥只在 PostgreSQL 上生效。
    """
    result = await db.execute(
        select(ChapterBody).where(ChapterBody.chapter_id == chapter_id).with_for_update()
    )
    return result.scalar_one_or_none()


async def head_revision(db, chapter_id: str) -> Optional[int]:
    """章节当前的正文版本号。

    取 ChapterBody.rev 与 max(ChapterVersion.rev) 的较大者：头行是权威，但快照表
    可能先落库（保存流程先写快照再更新头行），两者都要看才不会漏判。
    """
    body_rev = (
        await db.execute(select(ChapterBody.rev).where(ChapterBody.chapter_id == chapter_id))
    ).scalar_one_or_none()
    snapshot_rev = (
        await db.execute(
            select(func.max(ChapterVersion.rev)).where(ChapterVersion.chapter_id == chapter_id)
        )
    ).scalar_one_or_none()

    revisions = [rev for rev in (body_rev, snapshot_rev) if rev is not None]
    return max(revisions) if revisions else None


async def ensure_run_is_current(db, run: ConsistencyRun, *, lock: bool = False) -> None:
    """确认没有更新的正文版本；有则说明本 run 的结论已经过时。

    Args:
        lock: True 时先锁住正文头行再比较（提交前必须用），把检查与提交之间的
              竞态窗口关掉。
    """
    if lock:
        await lock_body_head(db, run.chapter_id)

    latest_rev = await head_revision(db, run.chapter_id)
    if latest_rev is not None and latest_rev > run.body_rev:
        raise StaleRevisionError(
            f"chapter {run.chapter_id} advanced from rev {run.body_rev} to {latest_rev}"
        )


async def advance_run_status(db, run_id: int, status: str, *, allowed_from: tuple[str, ...]) -> bool:
    """条件推进 run 状态，返回是否真的改动了。

    条件 UPDATE 而不是 `run.status = ...`：摘要与扫描并行，无条件赋值会让慢的一方
    把状态从 scanning/completed 打回 summarizing，前端看到进度倒退。
    """
    result = await db.execute(
        update(ConsistencyRun)
        .where(ConsistencyRun.id == run_id, ConsistencyRun.status.in_(allowed_from))
        .values(status=status, updated_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return result.rowcount > 0


async def fail_run(db, run_id: int, *, error_code: str, error_detail: str) -> None:
    """回滚未提交的工作，再把 run 标记为 failed。

    先 rollback 是必须的：抛出 stale/异常时事务里通常已经堆了半成品（新 claim、
    摘要行、supersede 过的旧行）。不回滚就提交失败标记，会把这些过期结论一起写进
    数据库 —— 正是「旧 worker 覆盖新版本」的那个缺陷。
    """
    await db.rollback()
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


def _revision_error_code(exc: Exception) -> str:
    return "body_not_found" if isinstance(exc, BodyRevisionMissingError) else "stale_revision"


async def _prepare_step(db, run_id: int) -> tuple[ConsistencyRun, ChapterVersion]:
    """每个步骤开始时的公共前置：加载 run、拒绝已失败的 run、取快照、校验版本。"""
    run = await load_run(db, run_id)
    await ensure_run_not_failed(db, run_id)

    try:
        snapshot = await load_body_snapshot(db, run)
        await ensure_run_is_current(db, run)
    except (BodyRevisionMissingError, StaleRevisionError) as exc:
        await fail_run(db, run_id, error_code=_revision_error_code(exc), error_detail=str(exc))
        raise

    return run, snapshot


def build_entity_linker() -> EntityLinker:
    """构造实体链接器（L2 别名 + L3 向量兜底）。

    单独抽成函数，测试里可以替换成只用别名的版本；生产走真实 embedding 网关。
    """
    return EntityLinker(ConsistencyRetrieval(GatewayEmbeddingProvider()))


@shared_task(bind=True, name="consistency.process_body_saved")
def process_body_saved(self, payload: dict):
    """
    处理 chapter.body_saved 事件 - 启动管道
    """
    return run_async(_process_body_saved_async(self.request.id, payload))


async def _process_body_saved_async(task_id: str, payload: dict):
    async with AsyncSessionLocal() as db:
        # get_or_create_run 内部处理唯一键竞态：两个 worker 同时收到同一次保存时，
        # 后到的一方读回已存在的 run，而不是撞 uq_consistency_run_key 崩掉。
        run_id = await get_or_create_run(
            db,
            project_id=payload["project_id"],
            chapter_id=payload["chapter_id"],
            body_rev=payload["body_rev"],
            pipeline_version=PIPELINE_VERSION,
            trigger=normalize_run_trigger(payload.get("trigger")),
        )
        await db.commit()

        # 抽取必须先完成（摘要与扫描都读它的产物），之后两条支线并行投递：
        # 扫描只依赖已落库的 claim，没有理由等摘要的模型调用。
        pipeline = chain(
            extract_claims.si(run_id),
            group(generate_summary.si(run_id), scan_rules.si(run_id)),
        )
        pipeline.apply_async()

        return {
            "status": "dispatched",
            "task_id": task_id,
            "run_id": run_id,
            "payload": payload,
        }


@shared_task(bind=True, name="consistency.extract_claims")
def extract_claims(self, run_id: int):
    """
    Step 1: Extract structured claims from the body revision
    """
    return run_async(_extract_claims_async(self.request.id, run_id))


async def _extract_claims_async(task_id: str, run_id: int):
    async with AsyncSessionLocal() as db:
        run, snapshot = await _prepare_step(db, run_id)
        await advance_run_status(db, run_id, "extracting", allowed_from=("pending",))

        provider = ConsistencyProvider()
        try:
            claims_data = await provider.extract_claims(
                content_html=snapshot.content_html,
                project_id=run.project_id,
                chapter_id=run.chapter_id,
            )

            # 新版本发布：同章旧版本的 claim 在同一事务里作废，避免新旧共存误报。
            # 只标 superseded，绝不删除 —— 架构 4.3 要求「旧正文版本的 claim 不删除，
            # 标为 superseded，确保告警可追溯」。
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

            # 同版本重放（重试、pipeline 重跑）：读出本版本已有的指纹，跳过它们。
            #
            # 早期实现在这里 DELETE 掉本版本的旧行，理由是 uq_claim_body_source 不含
            # status、标 superseded 不释放唯一键。但删除违反架构 4.3 的可追溯性要求，
            # 而且 GuardIssueEvidence.claim_id 是 ON DELETE SET NULL 的外键 —— 删掉
            # claim 会把已有告警的证据指针悄悄清空，作者看到的是一条没有出处的告警。
            #
            # 正确做法是不产生重复行：唯一键就是 (chapter_id, body_rev, fingerprint,
            # extractor_version)，同一指纹重放本来就该是同一行，跳过即幂等。
            existing_fingerprints = set(
                (
                    await db.execute(
                        select(ConsistencyClaim.fingerprint).where(
                            ConsistencyClaim.chapter_id == run.chapter_id,
                            ConsistencyClaim.source_kind == "body",
                            ConsistencyClaim.body_rev == run.body_rev,
                            ConsistencyClaim.extractor_version == EXTRACTOR_VERSION,
                        )
                    )
                )
                .scalars()
                .all()
            )

            # 解析 subject/object 的 entry_id：规则按 entry_id 分组，
            # 不解析就等于关掉跨章节冲突检测。
            linker = build_entity_linker()
            linked = []
            for claim_data in claims_data:
                subject_entry_id, object_entry_id = await linker.link_claim(
                    db, run.project_id, claim_data
                )
                linked.append(
                    {
                        **claim_data,
                        "subject_entry_id": subject_entry_id,
                        "object_entry_id": object_entry_id,
                    }
                )

            # story_order 只在这里产生：由时间线服务按**已确认的全局锚点**分配，
            # 模型给的任何顺序值都被覆盖。抽取是逐块的，模型编出来的序号只在块内
            # 有意义，而规则扫描是全项目范围的 —— 直接采信等于伪造顺序。
            # 没有共同锚点的 claim 保持 story_order=None，只进待确认列表。
            anchored = assign_story_orders(linked)

            # 再对已经有全局顺序的状态型 claim 闭合有效区间
            positioned = assign_narrative_positions(anchored)

            skipped = 0
            inserted = 0
            for claim_data in positioned:
                # 同版本重放时跳过已有指纹：同一指纹在同一版本就是同一行
                if claim_data["fingerprint"] in existing_fingerprints:
                    skipped += 1
                    continue
                inserted += 1
                db.add(
                    ConsistencyClaim(
                        project_id=run.project_id,
                        subject_entry_id=claim_data["subject_entry_id"],
                        subject_text=claim_data["subject_text"],
                        predicate=claim_data["predicate"],
                        object_type=claim_data["object_type"],
                        object_value=claim_data.get("object_value"),
                        object_entry_id=claim_data["object_entry_id"],
                        polarity=claim_data.get("polarity", "positive"),
                        certainty=claim_data.get("certainty", "explicit"),
                        source_kind="body",
                        chapter_id=run.chapter_id,
                        body_rev=run.body_rev,
                        paragraph_id=claim_data.get("paragraph_id"),
                        timeline_id=claim_data.get("timeline_id"),
                        story_order=claim_data.get("story_order"),
                        valid_from_order=claim_data.get("valid_from_order"),
                        valid_to_order=claim_data.get("valid_to_order"),
                        extractor_version=EXTRACTOR_VERSION,
                        confidence=claim_data.get("confidence", 0.9),
                        fingerprint=claim_data["fingerprint"],
                        status="accepted",
                    )
                )

            # 提交前锁住头行再确认版本，确认通过后 claim 与 supersede 一起落库
            await ensure_run_is_current(db, run, lock=True)
            await ensure_run_not_failed(db, run_id)
            await db.commit()

            # 顺序不可靠的 claim 照常落库，但不进硬规则 —— 计数单独报出来，
            # 便于观察抽取器的顺序判定质量。
            pending_order = sum(1 for c in positioned if c.get("story_order") is None)

            return {
                "status": "success",
                "task_id": task_id,
                "run_id": run_id,
                "claims_count": len(positioned),
                "inserted_claims": inserted,
                "superseded_claims": superseded.rowcount,
                "skipped_existing_claims": skipped,
                "pending_order_claims": pending_order,
                "entity_linking": linker.stats.as_dict(),
            }
        except StaleRevisionError as exc:
            await fail_run(db, run_id, error_code="stale_revision", error_detail=str(exc))
            raise
        except RunFailedError:
            await db.rollback()
            raise
        except Exception as exc:
            await fail_run(db, run_id, error_code="extraction_error", error_detail=str(exc))
            raise


@shared_task(bind=True, name="consistency.generate_summary")
def generate_summary(self, run_id: int):
    """
    Step 2a: Generate and persist the document summary (与扫描并行)
    """
    return run_async(_generate_summary_async(self.request.id, run_id))


async def _generate_summary_async(task_id: str, run_id: int):
    async with AsyncSessionLocal() as db:
        run, snapshot = await _prepare_step(db, run_id)
        await advance_run_status(db, run_id, "summarizing", allowed_from=("pending", "extracting"))

        provider = ConsistencyProvider()
        try:
            summary_text, token_count = await provider.generate_summary(
                content_html=snapshot.content_html,
                summary_type="chapter",
            )

            # 先看当前有效摘要是哪一版：迟到的旧版本任务不能覆盖新版本摘要，
            # 也不该留下任何半成品行，所以在写入之前就判掉。
            head_rev = (
                await db.execute(
                    select(func.max(DocumentSummary.source_rev)).where(
                        DocumentSummary.owner_type == "chapter",
                        DocumentSummary.owner_id == run.chapter_id,
                        DocumentSummary.status == "active",
                    )
                )
            ).scalar_one_or_none()

            if head_rev is not None and head_rev > run.body_rev:
                raise StaleRevisionError(
                    f"chapter {run.chapter_id} already has an active summary at rev {head_rev}"
                )

            # 原子切换有效摘要：只 supersede 比本版本旧的 active 行
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

            # 同版本重跑原地更新，并发插入撞 uq_document_summary_key 时读回已存在行
            await upsert_active_summary(
                db,
                owner_type="chapter",
                owner_id=run.chapter_id,
                source_rev=run.body_rev,
                summary_version=SUMMARY_VERSION,
                content=summary_text,
                model_id=settings.consistency_summary_model,
                token_count=token_count,
            )

            await ensure_run_is_current(db, run, lock=True)
            await ensure_run_not_failed(db, run_id)
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
        except RunFailedError:
            await db.rollback()
            raise
        except Exception as exc:
            await fail_run(db, run_id, error_code="summary_error", error_detail=str(exc))
            raise


@shared_task(bind=True, name="consistency.scan_rules")
def scan_rules(self, run_id: int):
    """
    Step 2b: Execute rule scanning (与摘要并行；只依赖已落库的 claim)
    """
    return run_async(_scan_rules_async(self.request.id, run_id))


async def _scan_rules_async(task_id: str, run_id: int):
    async with AsyncSessionLocal() as db:
        run, _ = await _prepare_step(db, run_id)
        await advance_run_status(
            db, run_id, "scanning", allowed_from=("pending", "extracting", "summarizing")
        )

        scanner = RuleScanner(rule_version=RULE_VERSION)
        try:
            issue_ids = await scanner.scan_chapter(
                db=db,
                run_id=run.id,
                project_id=run.project_id,
                chapter_id=run.chapter_id,
                body_rev=run.body_rev,
            )

            await ensure_run_is_current(db, run, lock=True)
            await ensure_run_not_failed(db, run_id)

            await db.execute(
                update(ConsistencyRun)
                .where(ConsistencyRun.id == run_id)
                .values(
                    status="completed",
                    finished_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()

            return {
                "status": "success",
                "task_id": task_id,
                "run_id": run_id,
                "issues_found": len(issue_ids),
            }
        except StaleRevisionError as exc:
            await fail_run(db, run_id, error_code="stale_revision", error_detail=str(exc))
            raise
        except RunFailedError:
            await db.rollback()
            raise
        except Exception as exc:
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
    "RunFailedError",
    "RunNotFoundError",
    "StaleRevisionError",
    "advance_run_status",
    "build_entity_linker",
    "dispatch_outbox",
    "extract_claims",
    "generate_summary",
    "head_revision",
    "lock_body_head",
    "process_body_saved",
    "scan_rules",
]
