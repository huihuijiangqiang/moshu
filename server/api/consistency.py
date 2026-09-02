"""
Consistency API - status, scan, issues, resolutions with authentication
"""
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional, get_args
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import ProjectPermission, get_current_user, verify_project_permission
from db.models_consistency import OutboxEvent
from db.models_consistency_extended import ConsistencyRun, GuardIssueEvidence, GuardResolution
from db.models_core import Chapter, ChapterBody, User
from db.models_guard import GuardIssue
from db.session import get_db
from services.consistency import PIPELINE_VERSION, get_or_create_run
from services.outbox import OutboxService

router = APIRouter(tags=["consistency"])


class ConsistencyPhases(BaseModel):
    """三条支线各自的状态（pending / running / succeeded / failed）。

    单个 status 表达不了并行支线：扫描成功而摘要仍在跑时，run 既不是 completed 也
    不是 failed。调用方要判断「摘要能不能用」必须看 summary，不能看 status。
    """

    extract: str
    summary: str
    scan: str


class ConsistencyStatusResponse(BaseModel):
    chapter_id: str
    body_rev: int
    pipeline_version: str
    status: str
    #: 三个阶段的独立状态；status=completed 蕴含三者都是 succeeded
    phases: ConsistencyPhases
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error_code: Optional[str] = None
    #: 首个失败原因的详情（只保留根因，后发生的连带失败不覆盖它）
    error_detail: Optional[str] = None


class ScanRequest(BaseModel):
    chapter_id: str
    body_rev: int
    trigger: str = "manual_scan"


class ScanResponse(BaseModel):
    run_id: int
    status: str


class IssueListItem(BaseModel):
    id: str
    chapter_id: str
    issue_type: str
    severity: str
    description: str
    status: str
    resolved: bool
    issue_rev: int
    confidence: float
    chapter_index: int
    chapter_title: str
    evidence: list[dict]
    actions: list[str]
    arbitration_status: str
    arbitration_confidence: Optional[float] = None
    arbitration_rationale: Optional[str] = None
    updated_at: str


class IssueDetailResponse(BaseModel):
    id: str
    project_id: str
    chapter_id: str
    entry_id: Optional[str]
    issue_type: str
    rule_version: str
    fingerprint: str
    severity: str
    confidence: float
    description: str
    evidence: dict
    anchor: dict
    actions: list[str]
    status: str
    issue_rev: int
    resolved: bool
    resolution: Optional[str]
    false_positive: bool
    arbitration_status: str
    arbitration_confidence: Optional[float]
    arbitration_rationale: Optional[str]
    arbitration_model: Optional[str]
    arbitration_version: Optional[str]
    arbitration_error: Optional[str]
    arbitrated_at: Optional[str]


#: GuardResolution.action 的 CHECK 约束允许值，必须与 ORM 保持一致。
ResolutionAction = Literal[
    "accept_old_fact",
    "accept_new_fact",
    "intentional_exception",
    "false_positive",
    "fixed_in_body",
    "defer",
]
VALID_RESOLUTION_ACTIONS: tuple[str, ...] = get_args(ResolutionAction)


class ResolveIssueRequest(BaseModel):
    action: ResolutionAction
    note: Optional[str] = None
    issue_rev: int


class ProjectScanResponse(BaseModel):
    queued: int
    run_ids: list[int]


class RunOverview(BaseModel):
    chapter_id: str
    chapter_index: int
    chapter_title: str
    body_rev: int
    status: str
    phases: ConsistencyPhases
    error_code: Optional[str] = None
    error_detail: Optional[str] = None
    updated_at: str


class ProjectConsistencyOverview(BaseModel):
    status: str
    queued: int
    running: int
    completed: int
    failed: int
    outbox_pending: int
    outbox_dead_letter: int
    latest_activity_at: Optional[str]
    runs: list[RunOverview]


ACTIVE_RUN_STATES = {"pending", "extracting", "summarizing", "scanning"}
# Celery's hard limit is 30 minutes.  Manual recovery must not reset a valid
# streamed extraction while it is still inside that execution envelope.
STALE_RUN_AFTER = timedelta(minutes=31)


async def prepare_manual_run(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    body_rev: int,
) -> tuple[int, bool]:
    """Return ``(run_id, should_enqueue)`` for a manual current-revision scan.

    Fresh active runs are left alone. Terminal runs and active runs older than
    the worker hard timeout are reset so a user can recover a failed or lost
    task without violating the unique run key.
    """
    result = await db.execute(
        select(ConsistencyRun).where(
            ConsistencyRun.chapter_id == chapter_id,
            ConsistencyRun.body_rev == body_rev,
            ConsistencyRun.pipeline_version == PIPELINE_VERSION,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is None:
        return (
            await get_or_create_run(
                db,
                project_id=project_id,
                chapter_id=chapter_id,
                body_rev=body_rev,
                pipeline_version=PIPELINE_VERSION,
                trigger="manual_scan",
            ),
            True,
        )

    updated_at = existing.updated_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    if existing.status in ACTIVE_RUN_STATES and updated_at >= datetime.now(timezone.utc) - STALE_RUN_AFTER:
        return existing.id, False

    await db.execute(
        update(ConsistencyRun)
        .where(ConsistencyRun.id == existing.id)
        .values(
            status="pending",
            extract_state="pending",
            summary_state="pending",
            scan_state="pending",
            trigger="manual_scan",
            started_at=None,
            finished_at=None,
            error_code=None,
            error_detail=None,
            updated_at=datetime.now(timezone.utc),
        )
    )
    return existing.id, True


@router.get("/status/{chapter_id}/{body_rev}", response_model=ConsistencyStatusResponse)
async def get_consistency_status(
    chapter_id: str,
    body_rev: int,
    pipeline_version: str = PIPELINE_VERSION,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取章节版本的一致性处理状态"""
    result = await db.execute(
        select(ConsistencyRun)
        .where(
            ConsistencyRun.chapter_id == chapter_id,
            ConsistencyRun.body_rev == body_rev,
            ConsistencyRun.pipeline_version == pipeline_version,
        )
        .limit(1)
    )
    run = result.scalar_one_or_none()

    if not run:
        raise HTTPException(status_code=404, detail="Consistency run not found")

    # Verify project access
    await verify_project_permission(run.project_id, ProjectPermission.VIEW, user, db)

    return ConsistencyStatusResponse(
        chapter_id=run.chapter_id,
        body_rev=run.body_rev,
        pipeline_version=run.pipeline_version,
        status=run.status,
        phases=ConsistencyPhases(
            extract=run.extract_state,
            summary=run.summary_state,
            scan=run.scan_state,
        ),
        started_at=run.started_at.isoformat() if run.started_at else None,
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        error_code=run.error_code,
        error_detail=run.error_detail,
    )


@router.post("/scan", response_model=ScanResponse)
async def trigger_consistency_scan(
    request: ScanRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """触发一致性扫描"""
    # Get chapter and verify access
    result = await db.execute(select(Chapter).where(Chapter.id == request.chapter_id))
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    await verify_project_permission(chapter.project_id, ProjectPermission.RUN_GUARD, user, db)

    run_id, should_enqueue = await prepare_manual_run(
        db,
        project_id=chapter.project_id,
        chapter_id=request.chapter_id,
        body_rev=request.body_rev,
    )

    if should_enqueue:
        await OutboxService.enqueue(
            db,
            topic="consistency.manual_scan",
            aggregate_id=f"{request.chapter_id}:{request.body_rev}:{uuid4().hex}",
            aggregate_rev=request.body_rev,
            payload={
                "run_id": run_id,
                "project_id": chapter.project_id,
                "chapter_id": request.chapter_id,
                "body_rev": request.body_rev,
                "trigger": "manual_scan",
            },
        )
    await db.commit()

    status_result = await db.execute(
        select(ConsistencyRun.status).where(ConsistencyRun.id == run_id)
    )

    return ScanResponse(
        run_id=run_id,
        status=status_result.scalar_one(),
    )


@router.post("/projects/{project_id}/scan", response_model=ProjectScanResponse)
async def trigger_project_scan(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """把作品内所有已有正文的当前版本加入一致性扫描队列。"""
    await verify_project_permission(project_id, ProjectPermission.RUN_GUARD, user, db)
    result = await db.execute(
        select(Chapter, ChapterBody)
        .join(ChapterBody, ChapterBody.chapter_id == Chapter.id)
        .where(Chapter.project_id == project_id, ChapterBody.rev > 0)
        .order_by(Chapter.idx)
    )

    run_ids: list[int] = []
    for chapter, body in result.all():
        run_id, should_enqueue = await prepare_manual_run(
            db,
            project_id=project_id,
            chapter_id=chapter.id,
            body_rev=body.rev,
        )
        if not should_enqueue:
            continue
        await OutboxService.enqueue(
            db,
            topic="consistency.manual_scan",
            aggregate_id=f"{chapter.id}:{body.rev}:{uuid4().hex}",
            aggregate_rev=body.rev,
            payload={
                "run_id": run_id,
                "project_id": project_id,
                "chapter_id": chapter.id,
                "body_rev": body.rev,
                "trigger": "manual_scan",
            },
        )
        run_ids.append(run_id)

    await db.commit()
    return ProjectScanResponse(queued=len(run_ids), run_ids=run_ids)


@router.get("/projects/{project_id}/overview", response_model=ProjectConsistencyOverview)
async def get_project_consistency_overview(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """返回每章最新 run 与 outbox 状态，供 Guard 展示真实运行进度。"""
    await verify_project_permission(project_id, ProjectPermission.VIEW, user, db)
    result = await db.execute(
        select(ConsistencyRun, Chapter)
        .join(Chapter, Chapter.id == ConsistencyRun.chapter_id)
        .where(ConsistencyRun.project_id == project_id)
        .order_by(ConsistencyRun.chapter_id, ConsistencyRun.body_rev.desc(), ConsistencyRun.id.desc())
    )
    latest_by_chapter: dict[str, tuple[ConsistencyRun, Chapter]] = {}
    for run, chapter in result.all():
        latest_by_chapter.setdefault(run.chapter_id, (run, chapter))

    rows = sorted(latest_by_chapter.values(), key=lambda item: item[1].idx)
    queued = sum(run.status == "pending" for run, _ in rows)
    running = sum(run.status in {"extracting", "summarizing", "scanning"} for run, _ in rows)
    completed = sum(run.status == "completed" for run, _ in rows)
    failed = sum(run.status == "failed" for run, _ in rows)

    outbox_result = await db.execute(
        select(OutboxEvent).where(OutboxEvent.topic.in_(("chapter.body_saved", "consistency.manual_scan")))
    )
    project_events = [
        event for event in outbox_result.scalars().all()
        if isinstance(event.payload, dict) and event.payload.get("project_id") == project_id
    ]
    outbox_pending = sum(event.status in {"pending", "dispatching"} for event in project_events)
    outbox_dead_letter = sum(event.status == "dead_letter" for event in project_events)

    latest_activity = max((run.updated_at for run, _ in rows), default=None)
    if running:
        overall = "running"
    elif outbox_pending or queued:
        overall = "queued"
    elif failed or outbox_dead_letter:
        overall = "failed"
    elif completed:
        overall = "completed"
    else:
        overall = "idle"

    return ProjectConsistencyOverview(
        status=overall,
        queued=queued,
        running=running,
        completed=completed,
        failed=failed,
        outbox_pending=outbox_pending,
        outbox_dead_letter=outbox_dead_letter,
        latest_activity_at=latest_activity.isoformat() if latest_activity else None,
        runs=[
            RunOverview(
                chapter_id=run.chapter_id,
                chapter_index=chapter.idx,
                chapter_title=chapter.title,
                body_rev=run.body_rev,
                status=run.status,
                phases=ConsistencyPhases(
                    extract=run.extract_state,
                    summary=run.summary_state,
                    scan=run.scan_state,
                ),
                error_code=run.error_code,
                error_detail=run.error_detail,
                updated_at=run.updated_at.isoformat(),
            )
            for run, chapter in rows
        ],
    )


@router.get("/issues/{project_id}", response_model=list[IssueListItem])
async def list_issues(
    project_id: str,
    chapter_id: Optional[str] = None,
    status_filter: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出一致性问题"""
    await verify_project_permission(project_id, ProjectPermission.VIEW, user, db)

    query = (
        select(GuardIssue, Chapter)
        .join(Chapter, Chapter.id == GuardIssue.chapter_id)
        .where(GuardIssue.project_id == project_id)
    )

    if chapter_id:
        query = query.where(GuardIssue.chapter_id == chapter_id)
    if status_filter:
        query = query.where(GuardIssue.status == status_filter)

    result = await db.execute(query)
    rows = result.all()
    issue_ids = [issue.id for issue, _ in rows]
    evidence_by_issue: dict[str, list[dict]] = {issue_id: [] for issue_id in issue_ids}
    if issue_ids:
        evidence_result = await db.execute(
            select(GuardIssueEvidence)
            .where(GuardIssueEvidence.issue_id.in_(issue_ids))
            .order_by(GuardIssueEvidence.issue_id, GuardIssueEvidence.sort_order)
        )
        labels = {"expected": "已有事实", "actual": "本次正文", "context": "相关上下文"}
        for evidence in evidence_result.scalars().all():
            evidence_by_issue[evidence.issue_id].append({
                "label": labels.get(evidence.side, evidence.side),
                "text": evidence.quote or "暂无可引用原文",
                "accent": evidence.side == "actual",
                "chapter_id": evidence.chapter_id,
                "paragraph_id": evidence.paragraph_id,
            })

    return [
        IssueListItem(
            id=issue.id,
            chapter_id=issue.chapter_id,
            issue_type=issue.issue_type,
            severity=issue.severity,
            description=issue.description,
            status=issue.status,
            resolved=issue.resolved,
            issue_rev=issue.issue_rev,
            confidence=float(issue.confidence),
            chapter_index=chapter.idx,
            chapter_title=chapter.title,
            evidence=evidence_by_issue[issue.id],
            actions=issue.actions,
            arbitration_status=issue.arbitration_status,
            arbitration_confidence=(
                float(issue.arbitration_confidence)
                if issue.arbitration_confidence is not None
                else None
            ),
            arbitration_rationale=issue.arbitration_rationale,
            updated_at=issue.updated_at.isoformat(),
        )
        for issue, chapter in rows
    ]


@router.get("/issues/{project_id}/{issue_id}", response_model=IssueDetailResponse)
async def get_issue_detail(
    project_id: str,
    issue_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取问题详情"""
    await verify_project_permission(project_id, ProjectPermission.VIEW, user, db)

    result = await db.execute(
        select(GuardIssue).where(
            GuardIssue.id == issue_id,
            GuardIssue.project_id == project_id,
        )
    )
    issue = result.scalar_one_or_none()

    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    return IssueDetailResponse(
        id=issue.id,
        project_id=issue.project_id,
        chapter_id=issue.chapter_id,
        entry_id=issue.entry_id,
        issue_type=issue.issue_type,
        rule_version=issue.rule_version,
        fingerprint=issue.fingerprint,
        severity=issue.severity,
        confidence=float(issue.confidence),
        description=issue.description,
        evidence=issue.evidence,
        anchor=issue.anchor,
        actions=issue.actions,
        status=issue.status,
        issue_rev=issue.issue_rev,
        resolved=issue.resolved,
        resolution=issue.resolution,
        false_positive=issue.false_positive,
        arbitration_status=issue.arbitration_status,
        arbitration_confidence=(
            float(issue.arbitration_confidence)
            if issue.arbitration_confidence is not None
            else None
        ),
        arbitration_rationale=issue.arbitration_rationale,
        arbitration_model=issue.arbitration_model,
        arbitration_version=issue.arbitration_version,
        arbitration_error=issue.arbitration_error,
        arbitrated_at=issue.arbitrated_at.isoformat() if issue.arbitrated_at else None,
    )


@router.post("/issues/{project_id}/{issue_id}/resolve")
async def resolve_issue(
    project_id: str,
    issue_id: str,
    request: ResolveIssueRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """处置一致性问题 - 使用 issue_rev 乐观锁"""
    await verify_project_permission(project_id, ProjectPermission.RESOLVE_GUARD, user, db)

    # 用带 issue_rev 条件的原子 UPDATE 实现乐观并发控制：
    # 并发的两个相同 issue_rev 请求里，只有一个能命中 rowcount==1。
    update_result = await db.execute(
        update(GuardIssue)
        .where(
            GuardIssue.id == issue_id,
            GuardIssue.project_id == project_id,
            GuardIssue.issue_rev == request.issue_rev,
        )
        .values(
            status="false_positive" if request.action == "false_positive" else "resolved",
            resolved=True,
            resolution=request.action,
            false_positive=request.action == "false_positive",
            issue_rev=GuardIssue.issue_rev + 1,
            updated_at=datetime.now(timezone.utc),
        )
    )

    if update_result.rowcount == 0:
        # 要么 issue 不存在（404），要么 issue_rev 不匹配（409）
        existing = await db.execute(
            select(GuardIssue.issue_rev).where(
                GuardIssue.id == issue_id,
                GuardIssue.project_id == project_id,
            )
        )
        current_rev = existing.scalar_one_or_none()
        if current_rev is None:
            await db.rollback()
            raise HTTPException(status_code=404, detail="Issue not found")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Issue revision mismatch: expected {request.issue_rev}, current {current_rev}",
        )

    # 处置记录绑定被处置的那个 issue_rev，created_by 是 ORM 的真实字段名
    db.add(
        GuardResolution(
            issue_id=issue_id,
            issue_rev=request.issue_rev,
            action=request.action,
            note=request.note,
            created_by=user.id,
        )
    )

    await db.commit()

    return {
        "status": "resolved",
        "action": request.action,
        "new_issue_rev": request.issue_rev + 1,
    }
