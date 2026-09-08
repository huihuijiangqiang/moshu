"""Naturalization review endpoints with paragraph-level optimistic locking."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import ProjectPermission, get_current_user, verify_project_permission
from config import settings
from db.models_core import Chapter, User
from db.models_naturalization import NaturalizationFinding, NaturalizationRun
from db.session import get_db
from services.generation import GenerationRoute
from services.model_configs import (
    CredentialDecryptionError,
    InvalidModelEndpointError,
    active_user_generation_route,
)
from services.naturalization import (
    AssistedNaturalizationGateway,
    NaturalizationAnchorError,
    NaturalizationError,
    NaturalizationProviderError,
    NaturalizationResponseError,
    NaturalizationStaleError,
    accept_finding,
    apply_assisted_candidates,
    degrade_assisted_run,
    prepare_assisted_naturalization,
    refresh_run_staleness,
    regenerate_finding,
    reject_finding,
    scan_naturalization,
)
from services.usage import (
    InsufficientCreditsError,
    UsageReservation,
    record_user_key_generation,
    release_reservation,
    reserve_generation,
    settle_generation,
)

router = APIRouter()


def get_assisted_naturalization_gateway() -> AssistedNaturalizationGateway:
    return AssistedNaturalizationGateway()


class NaturalizationScanRequest(BaseModel):
    chapter_id: str
    scope: Literal["chapter", "selection"] = "chapter"
    mode: Literal["rules", "assisted"] = "rules"
    paragraph_ids: list[str] = Field(default_factory=list, max_length=500)
    source_body_rev: int | None = Field(default=None, ge=0)
    style_profile_id: str | None = Field(default=None, max_length=32)


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
    gateway: AssistedNaturalizationGateway = Depends(get_assisted_naturalization_gateway),
) -> NaturalizationRunDetail:
    await verify_project_permission(project_id, ProjectPermission.EDIT_BODY, user, db)
    chapter = await db.scalar(select(Chapter).where(Chapter.id == request.chapter_id, Chapter.project_id == project_id, Chapter.deleted_at.is_(None)))
    if chapter is None:
        raise HTTPException(status_code=404, detail={"code": "CHAPTER_NOT_FOUND"})
    reservation: UsageReservation | None = None
    try:
        run = await scan_naturalization(
            db, user_id=user.id, project_id=project_id, chapter_id=request.chapter_id,
            scope=request.scope, mode=request.mode, paragraph_ids=request.paragraph_ids,
            source_body_rev=request.source_body_rev, style_profile_id=request.style_profile_id,
        )
        if request.mode == "assisted" and run.status == "scanning":
            try:
                user_route = await active_user_generation_route(db, user_id=user.id)
            except (CredentialDecryptionError, InvalidModelEndpointError) as exc:
                raise NaturalizationProviderError("自定义模型配置不可用，请检查后重试") from exc
            route = (
                GenerationRoute(
                    source="user",
                    endpoint=user_route.endpoint,
                    api_key=user_route.api_key,
                    model_id=user_route.model,
                    model_tier="custom",
                    config_id=user_route.config_id,
                )
                if user_route is not None
                else GenerationRoute(
                    source="platform",
                    endpoint=settings.gateway_url(settings.generation_gateway_tier),
                    api_key=settings.gateway_key(settings.generation_gateway_tier),
                    model_id=settings.resolved_generation_model,
                    model_tier=settings.generation_gateway_tier,
                )
            )
            package = await prepare_assisted_naturalization(db, run=run, route=route)
            assisted_run_id = package.run_id
            assisted_model = package.route.model_id
            # Persist immutable anchors and release the transaction before the
            # potentially slow model request.
            await db.commit()
            if package.route.source == "platform":
                try:
                    reservation = await reserve_generation(
                        db,
                        user_id=user.id,
                        project_id=project_id,
                        feature="naturalization_assisted",
                        model=package.route.model_id,
                        model_tier=package.route.model_tier,
                        prompt_tokens=package.prompt_tokens,
                        target_words=package.target_words,
                    )
                except InsufficientCreditsError as exc:
                    await degrade_assisted_run(
                        db,
                        run_id=assisted_run_id,
                        error_code="ASSISTED_INSUFFICIENT_CREDITS",
                        model=assisted_model,
                    )
                    await db.commit()
                    raise HTTPException(
                        status_code=402,
                        detail={
                            "code": "INSUFFICIENT_CREDITS",
                            "required": exc.required,
                            "remaining": exc.remaining,
                            "run_id": assisted_run_id,
                            "fallback": "rules",
                        },
                    ) from exc
            try:
                result = await gateway.suggest(package)
                run, generation_run = await apply_assisted_candidates(
                    db,
                    run_id=assisted_run_id,
                    result=result,
                    route=package.route,
                )
                if reservation is not None:
                    await settle_generation(
                        db,
                        reservation,
                        run_id=generation_run.id,
                        prompt_tokens=result.prompt_tokens,
                        cached_tokens=result.cached_tokens,
                        completion_tokens=result.completion_tokens,
                    )
                else:
                    await record_user_key_generation(
                        db,
                        user_id=user.id,
                        project_id=project_id,
                        feature="naturalization_assisted",
                        model=package.route.model_id,
                        run_id=generation_run.id,
                        config_id=package.route.config_id or "unknown",
                        prompt_tokens=result.prompt_tokens,
                        cached_tokens=result.cached_tokens,
                        completion_tokens=result.completion_tokens,
                    )
                await db.commit()
            except NaturalizationStaleError:
                await db.rollback()
                stale_run = await db.get(NaturalizationRun, assisted_run_id)
                if stale_run is not None:
                    await refresh_run_staleness(db, stale_run)
                if reservation is not None:
                    await release_reservation(db, reservation, reason="naturalization_stale")
                await db.commit()
                raise
            except Exception as exc:
                await db.rollback()
                if reservation is not None:
                    await release_reservation(db, reservation, reason="naturalization_assisted_failed")
                if isinstance(exc, NaturalizationResponseError):
                    error_code = exc.code
                elif isinstance(exc, NaturalizationProviderError):
                    error_code = exc.code
                else:
                    error_code = "NATURALIZATION_ASSISTED_INTERNAL_ERROR"
                run = await degrade_assisted_run(
                    db,
                    run_id=assisted_run_id,
                    error_code=error_code,
                    model=assisted_model,
                )
                await db.commit()
        else:
            await db.commit()
    except NaturalizationError as error:
        await db.rollback()
        raise _error(error) from error
    await db.refresh(run)
    findings = list((await db.execute(select(NaturalizationFinding).where(NaturalizationFinding.run_id == run.id).order_by(NaturalizationFinding.id))).scalars())
    for finding in findings:
        await db.refresh(finding)
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
