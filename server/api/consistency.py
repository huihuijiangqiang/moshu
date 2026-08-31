"""
Consistency API - status, scan, issues, resolutions with authentication
"""
from datetime import datetime, timezone
from typing import Literal, Optional, get_args

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, verify_project_access
from db.models_consistency_extended import ConsistencyRun, GuardResolution
from db.models_core import Chapter, User
from db.models_guard import GuardIssue
from db.session import get_db
from services.consistency import PIPELINE_VERSION, get_or_create_run
from services.outbox import OutboxService

router = APIRouter(tags=["consistency"])


class ConsistencyStatusResponse(BaseModel):
    chapter_id: str
    body_rev: int
    pipeline_version: str
    status: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error_code: Optional[str] = None


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
    await verify_project_access(run.project_id, user, db)

    return ConsistencyStatusResponse(
        chapter_id=run.chapter_id,
        body_rev=run.body_rev,
        pipeline_version=run.pipeline_version,
        status=run.status,
        started_at=run.started_at.isoformat() if run.started_at else None,
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        error_code=run.error_code,
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

    await verify_project_access(chapter.project_id, user, db)

    # get_or_create_run 返回 run_id(int)，且 pipeline_version 是必填参数
    run_id = await get_or_create_run(
        db,
        project_id=chapter.project_id,
        chapter_id=request.chapter_id,
        body_rev=request.body_rev,
        pipeline_version=PIPELINE_VERSION,
        trigger=request.trigger,
    )

    # OutboxService.enqueue 是 staticmethod，db 为第一个位置参数
    await OutboxService.enqueue(
        db,
        topic="consistency.manual_scan",
        aggregate_id=f"{request.chapter_id}:{request.body_rev}",
        aggregate_rev=request.body_rev,
        payload={
            "run_id": run_id,
            "project_id": chapter.project_id,
            "chapter_id": request.chapter_id,
            "body_rev": request.body_rev,
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


@router.get("/issues/{project_id}", response_model=list[IssueListItem])
async def list_issues(
    project_id: str,
    chapter_id: Optional[str] = None,
    status_filter: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出一致性问题"""
    await verify_project_access(project_id, user, db)

    query = select(GuardIssue).where(GuardIssue.project_id == project_id)

    if chapter_id:
        query = query.where(GuardIssue.chapter_id == chapter_id)
    if status_filter:
        query = query.where(GuardIssue.status == status_filter)

    result = await db.execute(query)
    issues = result.scalars().all()

    return [
        IssueListItem(
            id=issue.id,
            chapter_id=issue.chapter_id,
            issue_type=issue.issue_type,
            severity=issue.severity,
            description=issue.description,
            status=issue.status,
            resolved=issue.resolved,
        )
        for issue in issues
    ]


@router.get("/issues/{project_id}/{issue_id}", response_model=IssueDetailResponse)
async def get_issue_detail(
    project_id: str,
    issue_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取问题详情"""
    await verify_project_access(project_id, user, db)

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
    await verify_project_access(project_id, user, db)

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
