"""Chapter-outline API with explicit body-safety decisions."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import (
    ProjectPermission,
    get_current_user,
    verify_chapter_assignment,
    verify_project_permission,
)
from db.models_core import Chapter, User
from db.session import get_db
from domain.outlines import (
    BodyPolicy,
    BodyPolicyRequiredError,
    BodyRevisionResolutionConflictError,
)
from services.outlines import (
    ChapterNotFoundError,
    OutlineResult,
    OutlineRevisionConflictError,
    acknowledge_body_revision,
    list_outline_revisions,
    update_outline,
)

router = APIRouter()


class UpdateOutlineRequest(BaseModel):
    title: str = Field(max_length=200)
    nodes: list[str] = Field(max_length=100)
    note: str = Field(default="", max_length=20_000)
    base_outline_revision: int = Field(ge=0)
    body_policy: BodyPolicy | None = None


class OutlineResponse(BaseModel):
    chapter_id: str
    title: str
    nodes: list[str]
    note: str
    outline_revision: int
    outline_updated_at: str | None
    body_needs_revision: bool
    marked_outline_rev: int | None
    marked_body_rev: int | None


class OutlineRevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    revision: int
    title: str
    nodes: list[str]
    note: str
    body_policy: BodyPolicy
    body_rev_at_change: int | None
    created_by: str | None
    created_at: str


class AcknowledgeBodyRevisionRequest(BaseModel):
    base_body_rev: int = Field(ge=0)
    addressed_outline_revision: int = Field(ge=0)


def outline_response(result: OutlineResult) -> OutlineResponse:
    return OutlineResponse(
        chapter_id=result.chapter_id,
        title=result.content.title,
        nodes=list(result.content.nodes),
        note=result.content.note,
        outline_revision=result.state.revision,
        outline_updated_at=result.updated_at,
        body_needs_revision=result.state.body_needs_revision,
        marked_outline_rev=result.state.marked_outline_rev,
        marked_body_rev=result.state.marked_body_rev,
    )


async def _verify_chapter_access(
    db: AsyncSession,
    chapter_id: str,
    user: User,
    permission: ProjectPermission = ProjectPermission.VIEW,
) -> Chapter:
    result = await db.execute(
        select(Chapter).where(Chapter.id == chapter_id, Chapter.deleted_at.is_(None))
    )
    chapter = result.scalar_one_or_none()
    if chapter is None:
        raise HTTPException(status_code=404, detail={"code": "CHAPTER_NOT_FOUND"})
    await verify_project_permission(chapter.project_id, permission, user, db)
    if permission == ProjectPermission.MANAGE_OUTLINE:
        await verify_chapter_assignment(chapter, user, db)
    return chapter


@router.put("/{chapter_id}/outline", response_model=OutlineResponse)
async def put_outline(
    chapter_id: str,
    request: UpdateOutlineRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OutlineResponse:
    try:
        await _verify_chapter_access(db, chapter_id, user, ProjectPermission.MANAGE_OUTLINE)
        result = await update_outline(
            db,
            chapter_id=chapter_id,
            title=request.title,
            nodes=request.nodes,
            note=request.note,
            base_outline_revision=request.base_outline_revision,
            body_policy=request.body_policy,
            created_by=user.id,
        )
        await db.commit()
    except ChapterNotFoundError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail={"code": "CHAPTER_NOT_FOUND"}) from error
    except OutlineRevisionConflictError as error:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "OUTLINE_REVISION_CONFLICT",
                "server_revision": error.actual,
                "client_revision": error.expected,
                "server_outline": {
                    "title": error.current.title,
                    "nodes": list(error.current.nodes),
                    "note": error.current.note,
                },
            },
        ) from error
    except BodyPolicyRequiredError as error:
        await db.rollback()
        raise HTTPException(status_code=422, detail={"code": "BODY_POLICY_REQUIRED"}) from error
    return outline_response(result)


@router.get("/{chapter_id}/outline/revisions", response_model=list[OutlineRevisionResponse])
async def get_outline_revisions(
    chapter_id: str,
    before_revision: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[OutlineRevisionResponse]:
    await _verify_chapter_access(db, chapter_id, user)
    rows = await list_outline_revisions(
        db,
        chapter_id,
        before_revision=before_revision,
        limit=limit,
    )
    return [
        OutlineRevisionResponse(
            revision=row.revision,
            title=row.title,
            nodes=row.nodes,
            note=row.note,
            body_policy=BodyPolicy(row.body_policy),
            body_rev_at_change=row.body_rev_at_change,
            created_by=row.created_by,
            created_at=row.created_at.isoformat(),
        )
        for row in rows
    ]


@router.post("/{chapter_id}/body-revision/resolve", response_model=OutlineResponse)
async def resolve_body_revision(
    chapter_id: str,
    request: AcknowledgeBodyRevisionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OutlineResponse:
    try:
        await _verify_chapter_access(db, chapter_id, user, ProjectPermission.MANAGE_OUTLINE)
        result = await acknowledge_body_revision(
            db,
            chapter_id=chapter_id,
            base_body_rev=request.base_body_rev,
            addressed_outline_revision=request.addressed_outline_revision,
        )
        await db.commit()
    except ChapterNotFoundError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail={"code": "CHAPTER_NOT_FOUND"}) from error
    except BodyRevisionResolutionConflictError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail={"code": "BODY_REVISION_MARKER_CONFLICT"}) from error
    return outline_response(result)
