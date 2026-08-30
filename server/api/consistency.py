"""
Consistency API - status, scan, issues, resolutions
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_consistency_extended import ConsistencyClaim, ConsistencyRun, GuardIssueEvidence
from db.models_guard import GuardIssue
from db.session import get_db

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
    resolved: bool


class IssueDetailResponse(BaseModel):
    id: str
    project_id: str
    chapter_id: str
    entry_id: Optional[str]
    issue_type: str
    severity: str
    description: str
    evidence: dict
    anchor: dict
    actions: list[str]
    resolved: bool
    resolution: Optional[str]
    false_positive: bool


class ResolveIssueRequest(BaseModel):
    action: str
    note: Optional[str] = None


@router.get("/status/{chapter_id}/{body_rev}", response_model=ConsistencyStatusResponse)
async def get_consistency_status(
    chapter_id: str,
    body_rev: int,
    pipeline_version: str = "v1",
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
    db: AsyncSession = Depends(get_db),
):
    """触发一致性扫描（MVP: 返回占位响应，实际扫描由 Celery 任务执行）"""
    # MVP placeholder: 实际实现需要调用 Celery 任务
    return ScanResponse(
        run_id=0,
        status="not_implemented",
    )


@router.get("/issues/{project_id}", response_model=list[IssueListItem])
async def list_issues(
    project_id: str,
    chapter_id: Optional[str] = None,
    resolved: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
):
    """列出一致性问题"""
    query = select(GuardIssue).where(GuardIssue.project_id == project_id)

    if chapter_id:
        query = query.where(GuardIssue.chapter_id == chapter_id)
    if resolved is not None:
        query = query.where(GuardIssue.resolved == resolved)

    result = await db.execute(query)
    issues = result.scalars().all()

    return [
        IssueListItem(
            id=issue.id,
            chapter_id=issue.chapter_id,
            issue_type=issue.issue_type,
            severity=issue.severity,
            description=issue.description,
            resolved=issue.resolved,
        )
        for issue in issues
    ]


@router.get("/issues/{project_id}/{issue_id}", response_model=IssueDetailResponse)
async def get_issue_detail(
    project_id: str,
    issue_id: str,
    db: AsyncSession = Depends(get_db),
):
    """获取问题详情"""
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
        severity=issue.severity,
        description=issue.description,
        evidence=issue.evidence,
        anchor=issue.anchor,
        actions=issue.actions,
        resolved=issue.resolved,
        resolution=issue.resolution,
        false_positive=issue.false_positive,
    )


@router.post("/issues/{project_id}/{issue_id}/resolve")
async def resolve_issue(
    project_id: str,
    issue_id: str,
    request: ResolveIssueRequest,
    db: AsyncSession = Depends(get_db),
):
    """处置一致性问题（MVP: 占位实现）"""
    result = await db.execute(
        select(GuardIssue).where(
            GuardIssue.id == issue_id,
            GuardIssue.project_id == project_id,
        )
    )
    issue = result.scalar_one_or_none()

    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    # MVP placeholder: 实际实现需要创建 GuardResolution 记录并更新 issue
    issue.resolved = True
    issue.resolution = request.action
    await db.commit()

    return {"status": "resolved", "action": request.action}
