"""Naturalization review endpoints with paragraph-level optimistic locking."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import ProjectPermission, get_current_user, verify_project_permission
from db.models_core import Chapter, User
from db.models_naturalization import NaturalizationFinding, NaturalizationRun
from db.session import get_db
from services.naturalization import (
    NaturalizationAnchorError,
    NaturalizationError,
    NaturalizationStaleError,
    accept_finding,
    regenerate_finding,
    refresh_run_staleness,
    reject_finding,
    scan_naturalization,
)

router = APIRouter()


class NaturalizationScanRequest(BaseModel):
    chapter_id: str
    scope: Literal["chapter", "selection"] = "chapter"
    mode: Literal["rules"] = "rules"
    paragraph_ids: list[str] = Field(default_factory=list, max_length=500)
    source_body_rev: int | None = Field(default=None, ge=0)
    style_profile_id: str | None = None


class NaturalizationFindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    paragraph_id: str
    start: int
    end: int
    original_text: str
    candidate_text: str
    source_text_hash: str
    rule_ids: list[str]
    reasons: list[str]
    locked_facts: dict
    validation: dict
    status: str
    revision: int


class NaturalizationRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    project_id: str
    chapter_id: str
    source_body_rev: int
    source_content_hash: str
    scope: str
    mode: str
    status: str
    style_profile_id: str | None
    prompt_version: str
    model: str | None
    finding_count: int
    accepted_count: int
    error_code: str | None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NaturalizationRunDetail(NaturalizationRunOut):
    findings: list[NaturalizationFindingOut]


def _finding_out(finding: NaturalizationFinding) -> NaturalizationFindingOut:
    return NaturalizationFindingOut.model_validate(finding)


def _run_out(run: NaturalizationRun) -> NaturalizationRunOut:
    return NaturalizationRunOut.model_validate(run)


async def _run_for_project(run_id: str, project_id: str, user: User, db: AsyncSession) -> NaturalizationRun:
    run = await db.scalar(select(NaturalizationRun).where(NaturalizationRun.id == run_id, NaturalizationRun.project_id == project_id))
    if run is None:
        raise HTTPException(status_code=404, detail={"code": "NATURALIZATION_RUN_NOT_FOUND"})
    await verify_project_permission(project_id, ProjectPermission.VIEW, user, db)
    await refresh_run_staleness(db, run)
    return run


def _error(error: NaturalizationError) -> HTTPException:
    if isinstance(error, NaturalizationStaleError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": error.code, "message": str(error)})
    if isinstance(error, NaturalizationAnchorError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": error.code, "message": str(error)})
    if "not found" in str(error):
        return HTTPException(status_code=404, detail={"code": "NATURALIZATION_NOT_FOUND"})
    return HTTPException(status_code=422, detail={"code": error.code, "message": str(error)})


@router.post("/projects/{project_id}/naturalization/scans", response_model=NaturalizationRunDetail, status_code=201)
async def create_naturalization_scan(
    project_id: str,
    request: NaturalizationScanRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NaturalizationRunDetail:
    await verify_project_permission(project_id, ProjectPermission.EDIT_BODY, user, db)
    chapter = await db.scalar(select(Chapter).where(Chapter.id == request.chapter_id, Chapter.project_id == project_id, Chapter.deleted_at.is_(None)))
    if chapter is None:
        raise HTTPException(status_code=404, detail={"code": "CHAPTER_NOT_FOUND"})
    try:
        run = await scan_naturalization(
            db, user_id=user.id, project_id=project_id, chapter_id=request.chapter_id,
            scope=request.scope, mode=request.mode, paragraph_ids=request.paragraph_ids,
            source_body_rev=request.source_body_rev, style_profile_id=request.style_profile_id,
        )
        await db.commit()
    except NaturalizationError as error:
        await db.rollback()
        raise _error(error) from error
    findings = list((await db.execute(select(NaturalizationFinding).where(NaturalizationFinding.run_id == run.id).order_by(NaturalizationFinding.id))).scalars())
    return NaturalizationRunDetail(**_run_out(run).model_dump(), findings=[_finding_out(item) for item in findings])


@router.get("/projects/{project_id}/naturalization/runs", response_model=list[NaturalizationRunOut])
async def list_naturalization_runs(
    project_id: str,
    chapter_id: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[NaturalizationRunOut]:
    await verify_project_permission(project_id, ProjectPermission.VIEW, user, db)
    statement = select(NaturalizationRun).where(NaturalizationRun.project_id == project_id).order_by(NaturalizationRun.created_at.desc()).limit(limit)
    if chapter_id:
        statement = statement.where(NaturalizationRun.chapter_id == chapter_id)
    runs = list((await db.execute(statement)).scalars())
    for run in runs:
        await refresh_run_staleness(db, run)
    if runs:
        await db.commit()
    return [_run_out(run) for run in runs]


@router.get("/naturalization/runs/{run_id}", response_model=NaturalizationRunDetail)
async def get_naturalization_run(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NaturalizationRunDetail:
    run = await db.get(NaturalizationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"code": "NATURALIZATION_RUN_NOT_FOUND"})
    await verify_project_permission(run.project_id, ProjectPermission.VIEW, user, db)
    await refresh_run_staleness(db, run)
    await db.commit()
    findings = list((await db.execute(select(NaturalizationFinding).where(NaturalizationFinding.run_id == run.id).order_by(NaturalizationFinding.id))).scalars())
    return NaturalizationRunDetail(**_run_out(run).model_dump(), findings=[_finding_out(item) for item in findings])


async def _finding_for_run(run_id: str, finding_id: str, user: User, db: AsyncSession) -> tuple[NaturalizationRun, NaturalizationFinding]:
    run = await db.get(NaturalizationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"code": "NATURALIZATION_RUN_NOT_FOUND"})
    await verify_project_permission(run.project_id, ProjectPermission.EDIT_BODY, user, db)
    finding = await db.scalar(select(NaturalizationFinding).where(NaturalizationFinding.id == finding_id, NaturalizationFinding.run_id == run_id))
    if finding is None:
        raise HTTPException(status_code=404, detail={"code": "NATURALIZATION_FINDING_NOT_FOUND"})
    return run, finding


@router.post("/naturalization/runs/{run_id}/findings/{finding_id}/candidate", response_model=NaturalizationFindingOut)
async def regenerate_naturalization_candidate(
    run_id: str,
    finding_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NaturalizationFindingOut:
    _, finding = await _finding_for_run(run_id, finding_id, user, db)
    try:
        finding = await regenerate_finding(db, finding)
        await db.commit()
    except NaturalizationError as error:
        await db.rollback()
        raise _error(error) from error
    return _finding_out(finding)


@router.post("/naturalization/runs/{run_id}/findings/{finding_id}/accept", response_model=NaturalizationRunDetail)
async def accept_naturalization_candidate(
    run_id: str,
    finding_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NaturalizationRunDetail:
    run, _ = await _finding_for_run(run_id, finding_id, user, db)
    try:
        _, run, _ = await accept_finding(db, finding_id=finding_id, project_id=run.project_id)
        await db.commit()
    except NaturalizationError as error:
        await db.rollback()
        raise _error(error) from error
    findings = list((await db.execute(select(NaturalizationFinding).where(NaturalizationFinding.run_id == run.id).order_by(NaturalizationFinding.id))).scalars())
    return NaturalizationRunDetail(**_run_out(run).model_dump(), findings=[_finding_out(item) for item in findings])


@router.post("/naturalization/runs/{run_id}/findings/{finding_id}/reject", response_model=NaturalizationFindingOut)
async def reject_naturalization_candidate(
    run_id: str,
    finding_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NaturalizationFindingOut:
    run, _ = await _finding_for_run(run_id, finding_id, user, db)
    try:
        finding = await reject_finding(db, finding_id=finding_id, project_id=run.project_id)
        await db.commit()
    except NaturalizationError as error:
        await db.rollback()
        raise _error(error) from error
    return _finding_out(finding)
