"""Author-managed chapter-ending hook ledger."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import ProjectPermission, get_current_user, verify_project_permission
from db.models_core import User
from db.models_long_generation import StoryHook
from db.session import get_db
from services.story_hooks import (
    StoryHookConflictError,
    StoryHookNotFoundError,
    StoryHookValidationError,
    create_story_hook,
    list_story_hooks,
    update_story_hook,
)

router = APIRouter()
HookStatus = Literal["open", "deferred", "resolved", "abandoned"]


class StoryHookCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_chapter_id: str = Field(alias="sourceChapterId", min_length=1, max_length=32)
    hook_type: str = Field(alias="hookType", min_length=1, max_length=50)
    concrete_event: str = Field(alias="concreteEvent", min_length=1, max_length=4000)
    unresolved_question: str = Field(alias="unresolvedQuestion", min_length=1, max_length=2000)
    payoff_by_chapter: int | None = Field(None, alias="payoffByChapter", ge=1)


class StoryHookUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    expected_revision: int = Field(alias="expectedRevision", ge=1)
    hook_type: str | None = Field(None, alias="hookType", min_length=1, max_length=50)
    concrete_event: str | None = Field(None, alias="concreteEvent", min_length=1, max_length=4000)
    unresolved_question: str | None = Field(
        None,
        alias="unresolvedQuestion",
        min_length=1,
        max_length=2000,
    )
    payoff_by_chapter: int | None = Field(None, alias="payoffByChapter", ge=1)
    payoff_chapter_id: str | None = Field(None, alias="payoffChapterId", max_length=32)
    status: HookStatus | None = None
    resolution: str | None = Field(None, max_length=4000)


class StoryHookResponse(BaseModel):
    id: str
    project_id: str
    source_chapter_id: str
    payoff_chapter_id: str | None
    hook_type: str
    concrete_event: str
    unresolved_question: str
    payoff_by_chapter: int | None
    status: HookStatus
    novelty_signature: str
    resolution: str
    revision: int
    created_at: datetime
    updated_at: datetime


def _response(hook: StoryHook) -> StoryHookResponse:
    return StoryHookResponse.model_validate(hook, from_attributes=True)


def _raise_story_hook_error(error: Exception) -> None:
    if isinstance(error, StoryHookNotFoundError):
        raise HTTPException(status_code=404, detail={"code": "STORY_HOOK_NOT_FOUND"}) from error
    if isinstance(error, StoryHookConflictError):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STORY_HOOK_REVISION_CONFLICT",
                "currentRevision": error.current_revision,
            },
        ) from error
    if isinstance(error, StoryHookValidationError):
        raise HTTPException(
            status_code=422,
            detail={"code": "STORY_HOOK_INVALID", "message": str(error)},
        ) from error
    raise error


@router.get("/projects/{project_id}/hooks", response_model=list[StoryHookResponse])
async def get_story_hooks(
    project_id: str,
    status: list[HookStatus] | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[StoryHookResponse]:
    await verify_project_permission(project_id, ProjectPermission.VIEW, user, db)
    hooks = await list_story_hooks(db, project_id=project_id, statuses=set(status or []))
    return [_response(hook) for hook in hooks]


@router.post("/projects/{project_id}/hooks", response_model=StoryHookResponse, status_code=201)
async def post_story_hook(
    project_id: str,
    request: StoryHookCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StoryHookResponse:
    await verify_project_permission(project_id, ProjectPermission.MANAGE_OUTLINE, user, db)
    try:
        hook = await create_story_hook(
            db,
            project_id=project_id,
            **request.model_dump(by_alias=False),
        )
        await db.commit()
        await db.refresh(hook)
    except Exception as error:
        await db.rollback()
        _raise_story_hook_error(error)
    return _response(hook)


@router.patch("/projects/{project_id}/hooks/{hook_id}", response_model=StoryHookResponse)
async def patch_story_hook(
    project_id: str,
    hook_id: str,
    request: StoryHookUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StoryHookResponse:
    await verify_project_permission(project_id, ProjectPermission.MANAGE_OUTLINE, user, db)
    changes = request.model_dump(
        by_alias=False,
        exclude={"expected_revision"},
        exclude_unset=True,
    )
    try:
        hook = await update_story_hook(
            db,
            hook_id=hook_id,
            project_id=project_id,
            expected_revision=request.expected_revision,
            changes=changes,
        )
        await db.commit()
        await db.refresh(hook)
    except Exception as error:
        await db.rollback()
        _raise_story_hook_error(error)
    return _response(hook)
