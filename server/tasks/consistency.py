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
* 同一版本重跑是**集合替换**，用集合 diff 完成，**不删除**任何 claim 行：本次
  存在的原地更新并恢复 accepted，缺失的标 superseded，新出现的插入（见
  services.claim_set）。跳过已有指纹不成立 —— 那样上一次抽到、这一次没抽到的
  事实会留在当前版本里，重算出来的 entry_id/order/confidence 也永远刷不进去。
  删除同样不成立：架构 4.3 要求「旧正文版本的 claim 不删除，标为 superseded，
  确保告警可追溯」，而且 GuardIssueEvidence.claim_id 是 ON DELETE SET NULL 的
  外键 —— 删 claim 会把已有告警的证据指针悄悄清空。
* 摘要与扫描在抽取之后并行投递：扫描只依赖已落库的 claim，等摘要（一次或多次
  模型调用）纯属浪费，告警会被拖慢几十秒。
* story_order 只由 services.timeline 依据**已确认的全局锚点**分配，模型给的顺序值
  一律被覆盖。**绝不用 Chapter.idx 推导 story_order**，也不采信模型的块内序号 ——
  架构 4.3 明确 story_order 是故事世界事件顺序，不是「第几章」；而抽取逐块进行，
  块内序号跨块没有共同标尺，被全项目范围的规则拿去排序就是另一种伪造。没有共同
  锚点时保持 NULL，claim 进待确认列表，不参与依赖时序的硬规则。

关于 run 状态：摘要与扫描并行，一个 status 列表达不了两条支线，所以 run 上有三个
独立阶段列 extract_state / summary_state / scan_state（pending / running /
succeeded / failed）。completed 与 finished_at 只在三个阶段**全部** succeeded 时落库，
且由一条 UPDATE 的 CASE 判定 —— 「先读对方是否成功、再写自己成功」会让同时收尾的两
条支线都以为对方没完成，run 永远停在 scanning。数据库另有
ck_consistency_run_completed_phases 兜底：status='completed' 必须蕴含三阶段成功。
任一支线失败即 failed，且 error_code/error_detail 只保留第一个 —— 双失败不覆盖根因。
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional, TypeVar

from billiard.exceptions import SoftTimeLimitExceeded
from celery import chain, group, shared_task
from sqlalchemy import and_, case, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config import settings
from db import ChapterBody, ChapterVersion, ConsistencyClaim, ConsistencyRun, DocumentSummary
from db.models_consistency_extended import RUN_PHASE_COLUMNS, RUN_PHASES
from db.models_guard import GuardIssue
from providers.consistency import ConsistencyProvider
from services.arbitration import (
    apply_arbitration_failure,
    apply_arbitration_results,
    load_pending_arbitration_cases,
)
from services.claim_set import replace_body_claim_set
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
from services.provider_usage import record_platform_usage
from services.retrieval import ConsistencyRetrieval
from services.rule_scanner import RuleScanner
from services.temporal_reflow import (
    enqueue_temporal_rescans,
    lock_project_timeline,
    reflow_project_timeline,
)
from services.timeline import assign_story_orders

# Create async engine for tasks
engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
logger = logging.getLogger(__name__)
TaskResult = TypeVar("TaskResult")


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


def cancel_pending_async_work() -> None:
    """Cancel the coroutine left behind when a signal interrupts run_until_complete."""
    loop = asyncio.get_event_loop()
    pending = asyncio.all_tasks(loop)
    for task in pending:
        task.cancel()
    if pending:
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))


async def _fail_timed_out_step(run_id: int, phase: str) -> None:
    """Persist a Celery soft timeout so the run never remains visibly active forever."""
    async with AsyncSessionLocal() as db:
        await fail_run(
            db,
            run_id,
            phase=phase,
            error_code=f"{phase}_timeout",
            error_detail=f"{phase} exceeded the worker soft time limit",
        )


def run_step_task(
    *,
    task_id: str,
    run_id: int,
    phase: str,
    step: Callable[[str, int], Awaitable[TaskResult]],
) -> TaskResult:
    """Run one async pipeline step and make process-level timeouts observable.

    Celery delivers ``SoftTimeLimitExceeded`` to the synchronous task wrapper while
    the event loop is blocked in ``run_until_complete``.  The coroutine's ordinary
    ``except Exception`` block therefore cannot reliably persist the failure.
    """
    try:
        return run_async(step(task_id, run_id))
    except SoftTimeLimitExceeded:
        cancel_pending_async_work()
        try:
            run_async(_fail_timed_out_step(run_id, phase))
        except Exception:
            logger.exception(
                "failed to persist %s timeout for consistency run %s", phase, run_id
            )
        raise


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
    """条件推进粗粒度 status，返回是否真的改动了。

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


#: 阶段进入 running 时对应的粗粒度 status，以及允许被它推进的前置 status。
#: 粗粒度 status 只表达「进行到哪一步」，完成判定一律看三个阶段列。
PHASE_PROGRESS: dict[str, tuple[str, tuple[str, ...]]] = {
    "extract": ("extracting", ("pending",)),
    "summary": ("summarizing", ("pending", "extracting")),
    "scan": ("scanning", ("pending", "extracting", "summarizing")),
}


def phase_column(phase: str):
    """阶段状态列对应的 ORM 属性。"""
    return getattr(ConsistencyRun, RUN_PHASE_COLUMNS[phase])


async def start_phase(db, run_id: int, phase: str) -> None:
    """把阶段标成 running，并顺带推进粗粒度 status（不倒退）。

    已经 succeeded 的阶段不回退成 running：Celery 是至少一次投递，同一个任务会被
    重放，而 ck_consistency_run_completed_phases 要求 completed 蕴含三阶段成功 ——
    把 scan_state 打回 running 会让重放的任务直接撞 CHECK 约束。
    """
    attr = phase_column(phase)
    await db.execute(
        update(ConsistencyRun)
        .where(ConsistencyRun.id == run_id)
        .values(
            {
                RUN_PHASE_COLUMNS[phase]: case((attr == "succeeded", "succeeded"), else_="running"),
                "updated_at": datetime.now(timezone.utc),
            }
        )
    )
    coarse, allowed_from = PHASE_PROGRESS[phase]
    await advance_run_status(db, run_id, coarse, allowed_from=allowed_from)


async def succeed_phase(db, run_id: int, phase: str) -> bool:
    """标记阶段成功；三阶段齐活时在同一条 UPDATE 里落 completed/finished_at。

    返回 run 是否（已经）处于 completed。

    为什么必须是同一条语句：如果先读「另外两条支线成功了吗」再写自己的成功，两条
    支线几乎同时收尾时都会读到对方尚未成功，于是谁也不写 completed，run 永远停在
    scanning。这里让数据库在写自己那一列的同时比较另外两列的当前值 —— 无论哪种完成
    顺序，只有最后完成的那一方满足条件，且不需要额外的锁。

    不在这里提交：本支线的产出必须和阶段状态在同一个事务里落库。产出提交了、状态没
    提交，run 会永远等一条已经跑完的支线。
    """
    now = datetime.now(timezone.utc)
    others_succeeded = and_(
        *[phase_column(other) == "succeeded" for other in RUN_PHASES if other != phase]
    )
    # failed 是终态：即使本阶段重试成功，也不把 run 从 failed 翻成 completed。
    # （正常路径下 ensure_run_not_failed 已经拦住了，这里把不变量写进语句本身。）
    can_complete = and_(others_succeeded, ConsistencyRun.status != "failed")

    await db.execute(
        update(ConsistencyRun)
        .where(ConsistencyRun.id == run_id)
        .values(
            {
                RUN_PHASE_COLUMNS[phase]: "succeeded",
                "status": case((can_complete, "completed"), else_=ConsistencyRun.status),
                # 重放的任务不刷新 finished_at：第一次完成的时刻才是这一版的完成时刻
                "finished_at": case(
                    (can_complete, func.coalesce(ConsistencyRun.finished_at, now)),
                    else_=ConsistencyRun.finished_at,
                ),
                "updated_at": now,
            }
        )
    )

    status = (
        await db.execute(select(ConsistencyRun.status).where(ConsistencyRun.id == run_id))
    ).scalar_one_or_none()
    return status == "completed"


async def fail_run(db, run_id: int, *, phase: str, error_code: str, error_detail: str) -> None:
    """回滚未提交的工作，再把 run 与出错的阶段标记为 failed。

    先 rollback 是必须的：抛出 stale/异常时事务里通常已经堆了半成品（新 claim、
    摘要行、supersede 过的旧行）。不回滚就提交失败标记，会把这些过期结论一起写进
    数据库 —— 正是「旧 worker 覆盖新版本」的那个缺陷。

    error_code/error_detail 只写第一次：摘要与扫描并行，第二条支线往往是因为第一条
    已经失败才连带失败（甚至只是重放），无条件覆盖会把根因换成后发生的次要错误。
    finished_at 同理取第一个终态时刻。

    已经 succeeded 的阶段不会被改成 failed：那条支线的产出已经落库并且对本 body_rev
    仍然有效，重放的任务在网关抖动时失败不该把 completed 的 run 打回 failed。
    """
    await db.rollback()
    now = datetime.now(timezone.utc)
    first_failure = ConsistencyRun.error_code.is_(None)
    await db.execute(
        update(ConsistencyRun)
        .where(ConsistencyRun.id == run_id, phase_column(phase) != "succeeded")
        .values(
            {
                "status": "failed",
                RUN_PHASE_COLUMNS[phase]: "failed",
                "error_code": case((first_failure, error_code), else_=ConsistencyRun.error_code),
                "error_detail": case(
                    (first_failure, error_detail[:2000]), else_=ConsistencyRun.error_detail
                ),
                "finished_at": func.coalesce(ConsistencyRun.finished_at, now),
                "updated_at": now,
            }
        )
    )
    await db.commit()


def _revision_error_code(exc: Exception) -> str:
    return "body_not_found" if isinstance(exc, BodyRevisionMissingError) else "stale_revision"


async def _prepare_step(db, run_id: int, *, phase: str) -> tuple[ConsistencyRun, ChapterVersion]:
    """每个步骤开始时的公共前置：加载 run、拒绝已失败的 run、取快照、校验版本。"""
    run = await load_run(db, run_id)
    await ensure_run_not_failed(db, run_id)

    try:
        snapshot = await load_body_snapshot(db, run)
        await ensure_run_is_current(db, run)
    except (BodyRevisionMissingError, StaleRevisionError) as exc:
        await fail_run(
            db,
            run_id,
            phase=phase,
            error_code=_revision_error_code(exc),
            error_detail=str(exc),
        )
        raise

    return run, snapshot


def build_entity_linker() -> EntityLinker:
    """构造实体链接器（L2 别名 + L3 向量兜底）。

    单独抽成函数，测试里可以替换成只用别名的版本；生产走真实 embedding 网关。
    """
    return EntityLinker(ConsistencyRetrieval(GatewayEmbeddingProvider()))


async def persist_task_usage(
    *,
    project_id: str,
    feature: str,
    events: list[dict],
    task_id: str,
    consistency_run_id: int | None = None,
) -> None:
    """Persist telemetry independently so it cannot break the writing pipeline."""
    if not events:
        return
    try:
        async with AsyncSessionLocal() as usage_db:
            await record_platform_usage(
                usage_db,
                project_id=project_id,
                feature=feature,
                events=events,
                task_id=task_id,
                consistency_run_id=consistency_run_id,
            )
            await usage_db.commit()
    except Exception:
        logger.exception("failed to persist %s provider usage for task %s", feature, task_id)


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
        dispatch_run_pipeline(run_id)

        return {
            "status": "dispatched",
            "task_id": task_id,
            "run_id": run_id,
            "payload": payload,
        }


def dispatch_run_pipeline(run_id: int) -> None:
    """投递指定 run 的抽取、摘要和扫描链路。

    正文保存和手工重扫共用这一入口，避免两个 topic 的编排方式逐渐分叉。
    """
    pipeline = chain(
        extract_claims.si(run_id),
        group(
            generate_summary.si(run_id),
            chain(scan_rules.si(run_id), arbitrate_issues.si(run_id)),
        ),
    )
    pipeline.apply_async()


@shared_task(bind=True, name="consistency.extract_claims")
def extract_claims(self, run_id: int):
    """
    Step 1: Extract structured claims from the body revision
    """
    return run_step_task(
        task_id=self.request.id,
        run_id=run_id,
        phase="extract",
        step=_extract_claims_async,
    )


async def _extract_claims_async(task_id: str, run_id: int):
    async with AsyncSessionLocal() as db:
        run, snapshot = await _prepare_step(db, run_id, phase="extract")
        project_id = run.project_id
        await start_phase(db, run_id, "extract")

        provider = ConsistencyProvider()
        linker: EntityLinker | None = None
        try:
            claims_data = await provider.extract_claims(
                content_html=snapshot.content_html,
                project_id=project_id,
                chapter_id=run.chapter_id,
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

            # Provider 与 entity linker 可能包含慢网络调用，必须在项目锁外完成。
            # 从第一条 claim DML 起则统一先锁 Project，再按 claim id 锁行；手工 reflow
            # 也走相同顺序，避免两个章节各持有 claim 锁后互等项目锁形成死锁。
            await lock_project_timeline(db, project_id=project_id)

            # 新版本发布：同章旧版本的 claim 在同一事务里作废，避免新旧共存误报。
            # 只标 superseded，绝不删除 —— 架构 4.3 要求「旧正文版本的 claim 不删除，
            # 标为 superseded，确保告警可追溯」。
            superseded_older = await db.execute(
                update(ConsistencyClaim)
                .where(
                    ConsistencyClaim.chapter_id == run.chapter_id,
                    ConsistencyClaim.source_kind == "body",
                    ConsistencyClaim.body_rev < run.body_rev,
                    ConsistencyClaim.status.in_(["candidate", "accepted"]),
                )
                .values(status="superseded", updated_at=datetime.now(timezone.utc))
            )

            # 其他章节已经落库的、带全局顺序的事件可作为相对时间参照。排除本章，
            # 避免正文重放时上一版的同名事件与本次抽取形成假歧义。
            anchor_rows = (
                await db.execute(
                    select(
                        ConsistencyClaim.timeline_id,
                        ConsistencyClaim.story_order,
                        ConsistencyClaim.temporal_event_ref,
                    ).where(
                        ConsistencyClaim.project_id == run.project_id,
                        ConsistencyClaim.chapter_id != run.chapter_id,
                        ConsistencyClaim.status == "accepted",
                        ConsistencyClaim.story_order.is_not(None),
                        ConsistencyClaim.temporal_event_ref.is_not(None),
                    )
                )
            ).all()
            known_anchors = [
                {
                    "timeline_id": row.timeline_id,
                    "story_order": row.story_order,
                    "temporal_event_ref": row.temporal_event_ref,
                }
                for row in anchor_rows
            ]

            # story_order 只在这里产生：由时间线服务按**已确认的全局锚点**分配，
            # 模型给的任何顺序值都被覆盖。抽取是逐块的，模型编出来的序号只在块内
            # 有意义，而规则扫描是全项目范围的 —— 直接采信等于伪造顺序。
            # 没有共同锚点的 claim 保持 story_order=None，只进待确认列表。
            anchored = assign_story_orders(linked, known_anchors=known_anchors)

            # 再对已经有全局顺序的状态型 claim 闭合有效区间
            positioned = assign_narrative_positions(anchored)

            # 同版本重放（重试、pipeline 重跑、换模型）是**集合替换**，不是追加：
            # 本次存在的原地更新、缺失的标 superseded、新出现的插入。
            # 见 services.claim_set —— 那里解释了为什么删除和跳过都不成立。
            diff = await replace_body_claim_set(
                db,
                project_id=project_id,
                chapter_id=run.chapter_id,
                body_rev=run.body_rev,
                extractor_version=EXTRACTOR_VERSION,
                claims=positioned,
            )

            # An upstream anchor edit may change relative claims in any later chapter.
            # Reflow the accepted project graph in this same transaction so no scanner
            # can observe a half-updated timeline.
            temporal_reflow = await reflow_project_timeline(
                db,
                project_id=project_id,
            )
            temporal_rescan_run_ids = await enqueue_temporal_rescans(
                db,
                project_id=project_id,
                chapter_ids=temporal_reflow.affected_chapter_ids,
                cause_id=f"extract-run-{run_id}",
                exclude_chapter_id=run.chapter_id,
            )

            # 提交前锁住头行再确认版本，确认通过后 claim 与 supersede 一起落库
            await ensure_run_is_current(db, run, lock=True)
            await ensure_run_not_failed(db, run_id)
            # 阶段状态与产出同一事务提交：产出落库而状态没落库，run 会永远等这一步
            completed = await succeed_phase(db, run_id, "extract")
            await db.commit()
            await persist_task_usage(
                project_id=project_id,
                feature="consistency_extract",
                events=[*(getattr(provider, "usage_events", []) or []), *linker.usage_events],
                task_id=task_id,
                consistency_run_id=run_id,
            )

            # 顺序不可靠的 claim 照常落库，但不进硬规则 —— 计数单独报出来，
            # 便于观察抽取器的顺序判定质量。
            pending_order = sum(1 for c in positioned if c.get("story_order") is None)

            return {
                "status": "success",
                "task_id": task_id,
                "run_id": run_id,
                "run_completed": completed,
                "claims_count": len(positioned),
                "inserted_claims": diff.inserted,
                "updated_claims": diff.updated,
                "restored_claims": diff.restored,
                "dropped_claims": diff.superseded,
                "kept_rejected_claims": diff.kept_rejected,
                "superseded_claims": superseded_older.rowcount,
                "pending_order_claims": pending_order,
                "temporal_reflow": temporal_reflow.as_dict(),
                "temporal_rescans_queued": len(temporal_rescan_run_ids),
                "entity_linking": linker.stats.as_dict(),
            }
        except StaleRevisionError as exc:
            await fail_run(
                db, run_id, phase="extract", error_code="stale_revision", error_detail=str(exc)
            )
            await persist_task_usage(
                project_id=project_id,
                feature="consistency_extract",
                events=[
                    *(getattr(provider, "usage_events", []) or []),
                    *(linker.usage_events if linker is not None else []),
                ],
                task_id=task_id,
                consistency_run_id=run_id,
            )
            raise
        except RunFailedError:
            await db.rollback()
            await persist_task_usage(
                project_id=project_id,
                feature="consistency_extract",
                events=[
                    *(getattr(provider, "usage_events", []) or []),
                    *(linker.usage_events if linker is not None else []),
                ],
                task_id=task_id,
                consistency_run_id=run_id,
            )
            raise
        except Exception as exc:
            await fail_run(
                db, run_id, phase="extract", error_code="extraction_error", error_detail=str(exc)
            )
            await persist_task_usage(
                project_id=project_id,
                feature="consistency_extract",
                events=[
                    *(getattr(provider, "usage_events", []) or []),
                    *(linker.usage_events if linker is not None else []),
                ],
                task_id=task_id,
                consistency_run_id=run_id,
            )
            raise


@shared_task(bind=True, name="consistency.generate_summary")
def generate_summary(self, run_id: int):
    """
    Step 2a: Generate and persist the document summary (与扫描并行)
    """
    return run_step_task(
        task_id=self.request.id,
        run_id=run_id,
        phase="summary",
        step=_generate_summary_async,
    )


async def _generate_summary_async(task_id: str, run_id: int):
    async with AsyncSessionLocal() as db:
        run, snapshot = await _prepare_step(db, run_id, phase="summary")
        project_id = run.project_id
        await start_phase(db, run_id, "summary")

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
            completed = await succeed_phase(db, run_id, "summary")
            await db.commit()
            await persist_task_usage(
                project_id=project_id,
                feature="consistency_summary",
                events=getattr(provider, "usage_events", []) or [],
                task_id=task_id,
                consistency_run_id=run_id,
            )

            return {
                "status": "success",
                "task_id": task_id,
                "run_id": run_id,
                "run_completed": completed,
                "summary_length": len(summary_text),
            }
        except StaleRevisionError as exc:
            await fail_run(
                db, run_id, phase="summary", error_code="stale_revision", error_detail=str(exc)
            )
            await persist_task_usage(
                project_id=project_id,
                feature="consistency_summary",
                events=getattr(provider, "usage_events", []) or [],
                task_id=task_id,
                consistency_run_id=run_id,
            )
            raise
        except RunFailedError:
            await db.rollback()
            await persist_task_usage(
                project_id=project_id,
                feature="consistency_summary",
                events=getattr(provider, "usage_events", []) or [],
                task_id=task_id,
                consistency_run_id=run_id,
            )
            raise
        except Exception as exc:
            await fail_run(
                db, run_id, phase="summary", error_code="summary_error", error_detail=str(exc)
            )
            await persist_task_usage(
                project_id=project_id,
                feature="consistency_summary",
                events=getattr(provider, "usage_events", []) or [],
                task_id=task_id,
                consistency_run_id=run_id,
            )
            raise


@shared_task(bind=True, name="consistency.scan_rules")
def scan_rules(self, run_id: int):
    """
    Step 2b: Execute rule scanning (与摘要并行；只依赖已落库的 claim)
    """
    return run_step_task(
        task_id=self.request.id,
        run_id=run_id,
        phase="scan",
        step=_scan_rules_async,
    )


async def _scan_rules_async(task_id: str, run_id: int):
    async with AsyncSessionLocal() as db:
        run, _ = await _prepare_step(db, run_id, phase="scan")
        await start_phase(db, run_id, "scan")

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

            # 扫描成功不等于这一版处理完了：摘要支线可能还在跑，甚至随后失败。
            # completed / finished_at 由 succeed_phase 在三阶段齐活时统一落库。
            completed = await succeed_phase(db, run_id, "scan")
            await db.commit()

            return {
                "status": "success",
                "task_id": task_id,
                "run_id": run_id,
                "run_completed": completed,
                "issues_found": len(issue_ids),
                "claims_scanned": scanner.last_scan_claim_count,
                "claims_pruned": scanner.last_scan_pruned_count,
                "scan_scope": scanner.last_scan_scope,
            }
        except StaleRevisionError as exc:
            await fail_run(
                db, run_id, phase="scan", error_code="stale_revision", error_detail=str(exc)
            )
            raise
        except RunFailedError:
            await db.rollback()
            raise
        except Exception as exc:
            await fail_run(
                db, run_id, phase="scan", error_code="scan_error", error_detail=str(exc)
            )
            raise


@shared_task(bind=True, name="consistency.rescan_temporal_dependents")
def rescan_temporal_dependents(self, run_id: int):
    """Run deterministic rules after another chapter moved a time anchor."""
    return run_async(_rescan_temporal_dependents_async(self.request.id, run_id))


async def _rescan_temporal_dependents_async(task_id: str, run_id: int):
    async with AsyncSessionLocal() as db:
        run = await load_run(db, run_id)
        await ensure_run_is_current(db, run)
        scanner = RuleScanner(rule_version=RULE_VERSION)
        issue_ids = await scanner.scan_chapter(
            db=db,
            run_id=run.id,
            project_id=run.project_id,
            chapter_id=run.chapter_id,
            body_rev=run.body_rev,
        )
        await ensure_run_is_current(db, run, lock=True)
        await db.commit()
        arbitrate_issues.delay(run_id)
        return {
            "status": "success",
            "task_id": task_id,
            "run_id": run_id,
            "issues_found": len(issue_ids),
            "claims_scanned": scanner.last_scan_claim_count,
            "claims_pruned": scanner.last_scan_pruned_count,
            "scan_scope": scanner.last_scan_scope,
        }


@shared_task(bind=True, name="consistency.arbitrate_issues")
def arbitrate_issues(self, run_id: int):
    """Review newly created or materially changed rule issues without blocking them."""
    return run_async(_arbitrate_issues_async(self.request.id, run_id))


async def _arbitrate_issues_async(task_id: str, run_id: int):
    async with AsyncSessionLocal() as db:
        run = await load_run(db, run_id)
        project_id = run.project_id
        issues, cases = await load_pending_arbitration_cases(db, run_id=run_id)
        if not cases:
            await db.rollback()
            return {
                "status": "success",
                "task_id": task_id,
                "run_id": run_id,
                "issues_arbitrated": 0,
                "failed": 0,
            }

        # Release the read transaction before the model network call. Immutable
        # chapter versions keep every quote stable while no DB connection is held.
        issue_ids = [issue.id for issue in issues]
        await db.rollback()
        provider = ConsistencyProvider()
        try:
            decisions = await provider.arbitrate_conflicts(cases)
        except Exception as exc:
            logger.exception("conflict arbitration failed for run %s", run_id)
            current = list(
                (
                    await db.execute(
                        select(GuardIssue)
                        .where(
                            GuardIssue.id.in_(issue_ids),
                            GuardIssue.run_id == run_id,
                            GuardIssue.arbitration_status == "pending",
                            GuardIssue.false_positive.is_(False),
                        )
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            apply_arbitration_failure(current, exc)
            await db.commit()
            await persist_task_usage(
                project_id=project_id,
                feature="consistency_arbitration",
                events=getattr(provider, "usage_events", []) or [],
                task_id=task_id,
                consistency_run_id=run_id,
            )
            return {
                "status": "degraded",
                "task_id": task_id,
                "run_id": run_id,
                "issues_arbitrated": 0,
                "failed": len(current),
            }

        current = list(
            (
                await db.execute(
                    select(GuardIssue)
                    .where(
                        GuardIssue.id.in_(issue_ids),
                        GuardIssue.run_id == run_id,
                        GuardIssue.arbitration_status == "pending",
                        GuardIssue.false_positive.is_(False),
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        apply_arbitration_results(
            current,
            decisions,
            model=settings.resolved_arbitration_model,
        )
        await db.commit()
        await persist_task_usage(
            project_id=project_id,
            feature="consistency_arbitration",
            events=getattr(provider, "usage_events", []) or [],
            task_id=task_id,
            consistency_run_id=run_id,
        )
        failed = sum(issue.arbitration_status == "failed" for issue in current)
        return {
            "status": "degraded" if failed else "success",
            "task_id": task_id,
            "run_id": run_id,
            "issues_arbitrated": len(current) - failed,
            "failed": failed,
        }


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
                elif event.topic == "consistency.manual_scan":
                    dispatch_run_pipeline(int(event.payload["run_id"]))
                    await OutboxService.mark_sent(db, event.id, event.lease_token)
                    dispatched += 1
                elif event.topic == "consistency.timeline_rescan":
                    rescan_temporal_dependents.delay(int(event.payload["run_id"]))
                    await OutboxService.mark_sent(db, event.id, event.lease_token)
                    dispatched += 1
                elif event.topic == "chapter.outline_updated":
                    # Outline retrieval reads the authoritative database rows
                    # directly; there is no materialized cache to refresh yet.
                    # Acknowledge the event so it does not become a false DLQ.
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
    "PHASE_PROGRESS",
    "RUN_PHASES",
    "RunFailedError",
    "RunNotFoundError",
    "StaleRevisionError",
    "_arbitrate_issues_async",
    "_rescan_temporal_dependents_async",
    "advance_run_status",
    "arbitrate_issues",
    "build_entity_linker",
    "dispatch_run_pipeline",
    "dispatch_outbox",
    "extract_claims",
    "fail_run",
    "generate_summary",
    "head_revision",
    "lock_body_head",
    "phase_column",
    "process_body_saved",
    "rescan_temporal_dependents",
    "scan_rules",
    "start_phase",
    "succeed_phase",
]
