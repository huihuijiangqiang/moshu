"""Chapter scene-card API.

Scene cards are an additive planning layer.  Legacy ``Chapter.outline`` nodes
remain untouched; cards carry explicit chapter outline/body revisions instead.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import ProjectPermission, get_current_user, verify_chapter_assignment, verify_project_permission
from db.models_core import Chapter, User
from db.models_scene_cards import ChapterScene
from db.session import get_db
from domain.outlines import BodyPolicy
from services.scene_cards import (
    SceneBodyPolicyRequiredError,
    SceneChapterNotFoundError,
    SceneNotFoundError,
    SceneOrderError,
    ScenePlanRevisionConflictError,
    SceneReferenceError,
    SceneRevisionConflictError,
    archive_scene,
    create_scene,
    list_scenes,
    reorder_scenes,
    update_scene,
)

router = APIRouter()


class SceneFields(BaseModel):
    pov_entry_id: str | None = Field(default=None, max_length=32)
    location_entry_id: str | None = Field(default=None, max_length=32)
    goal: str = Field(default="", max_length=20_000)
    obstacle: str = Field(default="", max_length=20_000)
    turn: str = Field(default="", max_length=20_000)
    info_gain: str = Field(default="", max_length=20_000)
    emotion_shift: str = Field(default="", max_length=20_000)
    hook: str = Field(default="", max_length=20_000)
    status: str = Field(default="planning", pattern="^(planning|ready|written|needs_revision|archived)$")


class SceneCreateRequest(SceneFields):
    order: int = Field(default=1, ge=1)
    base_outline_rev: int = Field(ge=0)
    base_body_rev: int | None = Field(default=0, ge=0)
    body_policy: BodyPolicy | None = None


class SceneUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pov_entry_id: str | None = Field(default=None, max_length=32)
    location_entry_id: str | None = Field(default=None, max_length=32)
    goal: str | None = Field(default=None, max_length=20_000)
    obstacle: str | None = Field(default=None, max_length=20_000)
    turn: str | None = Field(default=None, max_length=20_000)
    info_gain: str | None = Field(default=None, max_length=20_000)
    emotion_shift: str | None = Field(default=None, max_length=20_000)
    hook: str | None = Field(default=None, max_length=20_000)
    status: str | None = Field(default=None, pattern="^(planning|ready|written|needs_revision|archived)$")
    expected_rev: int = Field(ge=1)
    base_outline_rev: int = Field(ge=0)
    base_body_rev: int | None = Field(default=0, ge=0)
    body_policy: BodyPolicy | None = None


class SceneReorderRequest(BaseModel):
    scene_ids: list[str] = Field(min_length=1, max_length=500)
    base_outline_rev: int = Field(ge=0)
    base_body_rev: int | None = Field(default=0, ge=0)
    body_policy: BodyPolicy | None = None


class SceneArchiveRequest(BaseModel):
    expected_rev: int = Field(ge=1)
    base_outline_rev: int = Field(ge=0)
    base_body_rev: int | None = Field(default=0, ge=0)
    body_policy: BodyPolicy | None = None


class SceneResponse(SceneFields):
    model_config = ConfigDict(from_attributes=True)

    id: str
    chapter_id: str
    order: int
    rev: int
    outline_rev: int
    body_rev: int | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _response(scene: ChapterScene) -> SceneResponse:
    return SceneResponse.model_validate(scene)


async def _chapter_access(
    chapter_id: str,
    db: AsyncSession,
    user: User,
    permission: ProjectPermission,
) -> Chapter:
    try:
        chapter = await db.get(Chapter, chapter_id)
    except Exception:
        chapter = None
    if chapter is None or chapter.deleted_at is not None:
        raise HTTPException(status_code=404, detail={"code": "CHAPTER_NOT_FOUND"})
    await verify_project_permission(chapter.project_id, permission, user, db)
    if permission is ProjectPermission.MANAGE_OUTLINE:
        await verify_chapter_assignment(chapter, user, db)
    return chapter


def _raise_scene_error(error: Exception) -> None:
    if isinstance(error, SceneChapterNotFoundError | SceneNotFoundError):
        raise HTTPException(status_code=404, detail={"code": "SCENE_NOT_FOUND"}) from error
    if isinstance(error, SceneRevisionConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "SCENE_REVISION_CONFLICT",
                "server_revision": error.actual,
                "client_revision": error.expected,
                "scene_id": error.scene.id,
            },
        ) from error
    if isinstance(error, ScenePlanRevisionConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "SCENE_PLAN_REVISION_CONFLICT",
                "server_outline_revision": error.actual_outline,
                "client_outline_revision": error.expected_outline,
                "server_body_revision": error.actual_body,
                "client_body_revision": error.expected_body,
            },
        ) from error
    if isinstance(error, SceneBodyPolicyRequiredError):
        raise HTTPException(status_code=422, detail={"code": "BODY_POLICY_REQUIRED"}) from error
    if isinstance(error, SceneOrderError):
        raise HTTPException(status_code=409, detail={"code": "SCENE_ORDER_CONFLICT", "message": str(error)}) from error
    if isinstance(error, SceneReferenceError):
        raise HTTPException(status_code=422, detail={"code": "SCENE_REFERENCE_INVALID", "message": str(error)}) from error
    raise error


@router.get("/chapters/{chapter_id}/scenes", response_model=list[SceneResponse])
async def get_scenes(
    chapter_id: str,
    include_archived: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[SceneResponse]:
    await _chapter_access(chapter_id, db, user, ProjectPermission.VIEW)
    try:
        scenes = await list_scenes(db, chapter_id, include_archived=include_archived)
    except (SceneChapterNotFoundError, SceneNotFoundError) as error:
        _raise_scene_error(error)
    return [_response(scene) for scene in scenes]


@router.post("/chapters/{chapter_id}/scenes", response_model=SceneResponse, status_code=201)
async def post_scene(
    chapter_id: str,
    request: SceneCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SceneResponse:
    chapter = await _chapter_access(chapter_id, db, user, ProjectPermission.MANAGE_OUTLINE)
    try:
        scene = await create_scene(
            db,
            chapter_id=chapter_id,
            order=request.order,
            fields=request.model_dump(exclude={"order", "base_outline_rev", "base_body_rev", "body_policy"}),
            base_outline_rev=request.base_outline_rev,
            base_body_rev=request.base_body_rev,
            body_policy=request.body_policy,
            project_id=chapter.project_id,
        )
        await db.commit()
        await db.refresh(scene)
    except Exception as error:
        await db.rollback()
        _raise_scene_error(error)
    return _response(scene)


@router.patch("/scenes/{scene_id}", response_model=SceneResponse)
async def patch_scene(
    scene_id: str,
    request: SceneUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SceneResponse:
    # Access is checked after loading the card in the service; first resolve its
    # chapter through a read-only query to avoid trusting a client project id.
    from sqlalchemy import select
    from db.models_scene_cards import ChapterScene

    existing = await db.scalar(select(ChapterScene).where(ChapterScene.id == scene_id))
    if existing is None:
        raise HTTPException(status_code=404, detail={"code": "SCENE_NOT_FOUND"})
    chapter = await _chapter_access(existing.chapter_id, db, user, ProjectPermission.MANAGE_OUTLINE)
    try:
        scene = await update_scene(
            db,
            scene_id_value=scene_id,
            expected_rev=request.expected_rev,
            fields=request.model_dump(exclude={"expected_rev", "base_outline_rev", "base_body_rev", "body_policy"}, exclude_unset=True),
            base_outline_rev=request.base_outline_rev,
            base_body_rev=request.base_body_rev,
            body_policy=request.body_policy,
        )
        await db.commit()
        await db.refresh(scene)
    except Exception as error:
        await db.rollback()
        _raise_scene_error(error)
    return _response(scene)


@router.post("/chapters/{chapter_id}/scenes/reorder", response_model=list[SceneResponse])
async def post_reorder(
    chapter_id: str,
    request: SceneReorderRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[SceneResponse]:
    await _chapter_access(chapter_id, db, user, ProjectPermission.MANAGE_OUTLINE)
    try:
        scenes = await reorder_scenes(
            db,
            chapter_id=chapter_id,
            scene_ids=request.scene_ids,
            base_outline_rev=request.base_outline_rev,
            base_body_rev=request.base_body_rev,
            body_policy=request.body_policy,
        )
        await db.commit()
        for scene in scenes:
            await db.refresh(scene)
    except Exception as error:
        await db.rollback()
        _raise_scene_error(error)
    return [_response(scene) for scene in scenes]


@router.post("/scenes/{scene_id}/archive", response_model=SceneResponse)
async def post_archive(
    scene_id: str,
    request: SceneArchiveRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SceneResponse:
    from sqlalchemy import select
    from db.models_scene_cards import ChapterScene

    existing = await db.scalar(select(ChapterScene).where(ChapterScene.id == scene_id))
    if existing is None:
        raise HTTPException(status_code=404, detail={"code": "SCENE_NOT_FOUND"})
    chapter = await _chapter_access(existing.chapter_id, db, user, ProjectPermission.MANAGE_OUTLINE)
    try:
        scene = await archive_scene(
            db,
            scene_id_value=scene_id,
            expected_rev=request.expected_rev,
            base_outline_rev=request.base_outline_rev,
            base_body_rev=request.base_body_rev,
            body_policy=request.body_policy,
        )
        await db.commit()
        await db.refresh(scene)
    except Exception as error:
        await db.rollback()
        _raise_scene_error(error)
    return _response(scene)
