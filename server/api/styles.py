"""Authenticated style profile CRUD, extraction, and project binding."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import ProjectPermission, get_current_user, verify_project_permission
from config import settings
from db.models_core import Project, User
from db.models_usage import StyleProfile
from db.session import get_db
from services.style_profiles import (
    StyleExtractionError,
    StyleExtractionGateway,
    count_sample_words,
)
from services.usage import (
    InsufficientCreditsError,
    release_reservation,
    reserve_generation,
    settle_generation,
)

router = APIRouter()
MIN_EXTRACT_WORDS = 5_000
MAX_SAMPLE_CHARS = 500_000


class StyleProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sample_text: str = Field(min_length=1, max_length=MAX_SAMPLE_CHARS)
    is_default: bool = False

    @field_validator("name", "sample_text")
    @classmethod
    def non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value cannot be blank")
        return value


class StyleProfilePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    sample_text: str | None = Field(default=None, min_length=1, max_length=MAX_SAMPLE_CHARS)
    is_default: bool | None = None

    @field_validator("name", "sample_text")
    @classmethod
    def non_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("value cannot be blank")
        return value


class StyleProfileOut(BaseModel):
    id: str
    name: str
    sample_words: int
    is_default: bool
    dimensions: dict
    status: str
    confidence: str
    error_detail: str | None
    extracted_at: str | None
    created_at: str
    updated_at: str
    bound_project_ids: list[str]


class StyleBindingRequest(BaseModel):
    style_profile_id: str | None = Field(default=None, max_length=32)


class StyleBindingOut(BaseModel):
    project_id: str
    style_profile_id: str | None


def _confidence(words: int) -> str:
    if words < MIN_EXTRACT_WORDS:
        return "insufficient"
    return "standard" if words >= 50_000 else "low"


async def _profile_out(db: AsyncSession, profile: StyleProfile) -> StyleProfileOut:
    await db.refresh(profile)
    bound = (
        await db.execute(
            select(Project.id)
            .where(Project.owner_id == profile.user_id, Project.style_profile_id == profile.id)
            .order_by(Project.updated_at.desc())
        )
    ).scalars().all()
    return StyleProfileOut(
        id=profile.id,
        name=profile.name,
        sample_words=profile.sample_words,
        is_default=profile.is_default,
        dimensions=profile.dimensions or {},
        status=profile.status,
        confidence=_confidence(profile.sample_words),
        error_detail=profile.error_detail,
        extracted_at=profile.extracted_at.isoformat() if profile.extracted_at else None,
        created_at=profile.created_at.isoformat(),
        updated_at=profile.updated_at.isoformat(),
        bound_project_ids=list(bound),
    )


async def _owned_profile(db: AsyncSession, profile_id: str, user_id: str) -> StyleProfile:
    profile = await db.scalar(
        select(StyleProfile).where(StyleProfile.id == profile_id, StyleProfile.user_id == user_id)
    )
    if profile is None:
        raise HTTPException(status_code=404, detail={"code": "STYLE_PROFILE_NOT_FOUND"})
    return profile


async def _set_default(db: AsyncSession, profile: StyleProfile) -> None:
    await db.execute(
        update(StyleProfile)
        .where(StyleProfile.user_id == profile.user_id, StyleProfile.id != profile.id)
        .values(is_default=False)
    )
    profile.is_default = True


@router.get("/styles", response_model=list[StyleProfileOut])
async def list_style_profiles(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[StyleProfileOut]:
    profiles = (
        await db.execute(
            select(StyleProfile)
            .where(StyleProfile.user_id == user.id)
            .order_by(StyleProfile.is_default.desc(), StyleProfile.updated_at.desc())
        )
    ).scalars().all()
    return [await _profile_out(db, profile) for profile in profiles]


@router.post("/styles", response_model=StyleProfileOut, status_code=201)
async def create_style_profile(
    request: StyleProfileCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StyleProfileOut:
    has_profile = await db.scalar(
        select(StyleProfile.id).where(StyleProfile.user_id == user.id).limit(1)
    )
    profile = StyleProfile(
        id=f"sp_{secrets.token_hex(12)}",
        user_id=user.id,
        name=request.name,
        sample_text=request.sample_text,
        sample_words=count_sample_words(request.sample_text),
        is_default=False,
        dimensions={},
        alignment=0.0,
        status="pending",
    )
    db.add(profile)
    await db.flush()
    if request.is_default or has_profile is None:
        await _set_default(db, profile)
    await db.commit()
    return await _profile_out(db, profile)


@router.get("/styles/{profile_id}", response_model=StyleProfileOut)
async def get_style_profile(
    profile_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StyleProfileOut:
    return await _profile_out(db, await _owned_profile(db, profile_id, user.id))


@router.patch("/styles/{profile_id}", response_model=StyleProfileOut)
async def update_style_profile(
    profile_id: str,
    request: StyleProfilePatch,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StyleProfileOut:
    profile = await _owned_profile(db, profile_id, user.id)
    if profile.status == "processing":
        raise HTTPException(status_code=409, detail={"code": "STYLE_PROFILE_PROCESSING"})
    if request.name is not None:
        profile.name = request.name
    if request.sample_text is not None and request.sample_text != profile.sample_text:
        profile.sample_text = request.sample_text
        profile.sample_words = count_sample_words(request.sample_text)
        profile.dimensions = {}
        profile.status = "pending"
        profile.error_detail = None
        profile.extracted_at = None
    if request.is_default is True:
        await _set_default(db, profile)
    elif request.is_default is False and profile.is_default:
        raise HTTPException(status_code=409, detail={"code": "DEFAULT_PROFILE_REQUIRED"})
    await db.commit()
    return await _profile_out(db, profile)


@router.delete("/styles/{profile_id}", status_code=204)
async def delete_style_profile(
    profile_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    profile = await _owned_profile(db, profile_id, user.id)
    if profile.status == "processing":
        raise HTTPException(status_code=409, detail={"code": "STYLE_PROFILE_PROCESSING"})
    was_default = profile.is_default
    await db.delete(profile)
    await db.flush()
    if was_default:
        replacement = await db.scalar(
            select(StyleProfile)
            .where(StyleProfile.user_id == user.id)
            .order_by(StyleProfile.updated_at.desc())
            .limit(1)
        )
        if replacement:
            replacement.is_default = True
    await db.commit()
    return Response(status_code=204)


@router.post("/styles/{profile_id}/extract", response_model=StyleProfileOut)
async def extract_style_profile(
    profile_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StyleProfileOut:
    profile = await _owned_profile(db, profile_id, user.id)
    if profile.status == "processing":
        raise HTTPException(status_code=409, detail={"code": "STYLE_PROFILE_PROCESSING"})
    if profile.sample_words < MIN_EXTRACT_WORDS:
        raise HTTPException(
            status_code=422,
            detail={"code": "STYLE_SAMPLE_TOO_SHORT", "minimum": MIN_EXTRACT_WORDS},
        )
    gateway = StyleExtractionGateway()
    try:
        reservation = await reserve_generation(
            db,
            user_id=user.id,
            project_id=None,
            feature="style_extract",
            model=settings.resolved_generation_model,
            model_tier=settings.generation_gateway_tier,
            prompt_tokens=gateway.estimated_prompt_tokens(profile.sample_text),
            target_words=1_200,
        )
    except InsufficientCreditsError as exc:
        raise HTTPException(
            status_code=402,
            detail={"code": "INSUFFICIENT_CREDITS", "required": exc.required, "remaining": exc.remaining},
        ) from exc

    profile.status = "processing"
    profile.error_detail = None
    await db.commit()
    try:
        analysis = await gateway.analyze(profile.sample_text)
        profile.dimensions = analysis.dimensions
        profile.status = "ready"
        profile.extracted_at = datetime.now(UTC)
        await settle_generation(
            db,
            reservation,
            run_id=None,
            prompt_tokens=analysis.prompt_tokens,
            cached_tokens=analysis.cached_tokens,
            completion_tokens=analysis.completion_tokens,
        )
        await db.commit()
    except Exception as exc:
        await release_reservation(db, reservation, reason="style_extract_failed")
        profile.status = "failed"
        profile.error_detail = str(exc)[:500]
        await db.commit()
        detail = "风格抽取失败，请检查模型网关后重试"
        if isinstance(exc, StyleExtractionError):
            detail = str(exc)
        raise HTTPException(status_code=502, detail={"code": "STYLE_EXTRACTION_FAILED", "message": detail}) from exc
    return await _profile_out(db, profile)


@router.put("/projects/{project_id}/style-profile", response_model=StyleBindingOut)
async def bind_style_profile(
    project_id: str,
    request: StyleBindingRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StyleBindingOut:
    project = await verify_project_permission(project_id, ProjectPermission.MANAGE_PROJECT, user, db)
    if project.owner_id != user.id:
        raise HTTPException(status_code=403, detail={"code": "STYLE_BINDING_OWNER_ONLY"})
    if request.style_profile_id is not None:
        profile = await _owned_profile(db, request.style_profile_id, user.id)
        if profile.status != "ready":
            raise HTTPException(status_code=409, detail={"code": "STYLE_PROFILE_NOT_READY"})
    project.style_profile_id = request.style_profile_id
    await db.commit()
    return StyleBindingOut(project_id=project.id, style_profile_id=project.style_profile_id)
