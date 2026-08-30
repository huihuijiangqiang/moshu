"""
Consistency API - status, scan, issues, resolutions with authentication
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, verify_project_access
from db.models_consistency_extended import ConsistencyRun
from db.models_core import User
from db.models_guard import GuardIssue, GuardResolution
from db.session import get_db
from services.outbox import OutboxService

router = APIRouter(prefix="/consistency", tags=["consistency"])


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


class ResolveIssueRequest(BaseModel):
    action: str
    note: Optional[str] = None
    issue_rev: int


@router.get("/status/{chapter_id}/{body_rev}", response_model=ConsistencyStatusResponse)
async def get_consistency_status(
    chapter_id: str,
    body_rev: int,
    pipeline_version: str = "v1",
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
    from db.models_core import Chapter
    result = await db.execute(select(Chapter).where(Chapter.id == request.chapter_id))
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    await verify_project_access(chapter.project_id, user, db)

    # Get or create run
    from services.consistency import get_or_create_run
    run = await get_or_create_run(
        db,
        project_id=chapter.project_id,
        chapter_id=request.chapter_id,
        body_rev=request.body_rev,
        trigger=request.trigger,
    )

    # Enqueue work
    outbox_service = OutboxService(db)
    await outbox_service.enqueue(
        topic="consistency.manual_scan",
        aggregate_id=f"{request.chapter_id}:{request.body_rev}",
        aggregate_rev=request.body_rev,
        payload={
            "run_id": run.id,
            "project_id": chapter.project_id,
            "chapter_id": request.chapter_id,
            "body_rev": request.body_rev,
        },
    )
    await db.commit()

    return ScanResponse(
        run_id=run.id,
        status=run.status,
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

    result = await db.execute(
        select(GuardIssue).where(
            GuardIssue.id == issue_id,
            GuardIssue.project_id == project_id,
        )
    )
    issue = result.scalar_one_or_none()

    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    # Optimistic locking check
    if issue.issue_rev != request.issue_rev:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Issue revision mismatch: expected {request.issue_rev}, current {issue.issue_rev}",
        )

    # Create resolution record
    resolution = GuardResolution(
        issue_id=issue.id,
        action=request.action,
        user_id=user.id,
        note=request.note,
    )
    db.add(resolution)

    # Update issue
    issue.status = "resolved"
    issue.resolved = True
    issue.resolution = request.action
    issue.issue_rev += 1
    issue.updated_at = datetime.now(timezone.utc)

    await db.commit()

    return {"status": "resolved", "action": request.action, "new_issue_rev": issue.issue_rev}
