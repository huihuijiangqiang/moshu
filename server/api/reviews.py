"""Chapter review and paragraph comment API."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import (
    ProjectPermission,
    get_current_user,
    get_project_permissions,
    verify_chapter_assignment,
)
from db.models_core import Chapter, ChapterBody, User
from db.models_review import ChapterReviewRound, ReviewComment
from db.session import get_db
from services.reviews import (
    ReviewRevisionConflictError,
    ReviewWorkflowError,
    add_comment,
    decide_round,
    resolve_comment,
    submit_chapter,
    update_comment,
)

router = APIRouter()


class SubmitReviewRequest(BaseModel):
    submit_note: str | None = Field(default=None, max_length=4000)


class CommentCreateRequest(BaseModel):
    paragraph_id: str = Field(min_length=1, max_length=120)
    selected_text: str | None = Field(default=None, max_length=2000)
    content: str = Field(min_length=1, max_length=8000)


class CommentUpdateRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=8000)


class RevisionRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class ReviewDecisionRequest(RevisionRequest):
    decision: Literal["approved", "changes_requested"]
    decision_note: str | None = Field(default=None, max_length=8000)


class ReviewCommentOut(BaseModel):
    id: str
    round_id: str
    chapter_id: str
    body_revision: int
    paragraph_id: str
    paragraph_excerpt: str
    selected_text: str | None
    content: str
    status: str
    revision: int
    author_id: str | None
    author_name: str | None
    resolved_by: str | None
    created_at: str
    resolved_at: str | None


class ReviewRoundOut(BaseModel):
    id: str
    chapter_id: str
    submitted_body_revision: int
    status: str
    submit_note: str | None
    decision_note: str | None
    revision: int
    submitted_by: str | None
    submitted_by_name: str | None
    reviewed_by: str | None
    reviewed_by_name: str | None
    submitted_at: str
    reviewed_at: str | None
    stale: bool
    comments: list[ReviewCommentOut]


class ReviewWorkspaceOut(BaseModel):
    chapter_id: str
    current_body_revision: int
    can_submit: bool
    can_review: bool
    can_resolve: bool
    rounds: list[ReviewRoundOut]


def _workflow_http_error(error: ReviewWorkflowError) -> HTTPException:
    if isinstance(error, ReviewRevisionConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": error.code, "current_revision": error.current_revision},
        )
    if error.code in {"CHAPTER_NOT_FOUND", "REVIEW_ROUND_NOT_FOUND", "REVIEW_COMMENT_NOT_FOUND"}:
        code = status.HTTP_404_NOT_FOUND
    elif error.code in {
        "NEW_BODY_REVISION_REQUIRED",
        "REVIEW_ROUND_CLOSED",
        "REVIEW_COMMENT_RESOLVED",
        "OPEN_COMMENTS_PREVENT_APPROVAL",
        "REVIEW_ROUND_STALE",
    }:
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
    return HTTPException(status_code=code, detail={"code": error.code})


async def _permissions(project_id: str, user: User, db: AsyncSession) -> frozenset[ProjectPermission]:
    _, permissions = await get_project_permissions(project_id, user, db)
    return permissions


async def _require(
    project_id: str, permission: ProjectPermission, user: User, db: AsyncSession
) -> frozenset[ProjectPermission]:
    permissions = await _permissions(project_id, user, db)
    if permission not in permissions:
        raise HTTPException(status_code=403, detail="Access denied")
    return permissions


async def _serialize_workspace(
    project_id: str,
    chapter_id: str,
    permissions: frozenset[ProjectPermission],
    db: AsyncSession,
) -> ReviewWorkspaceOut:
    chapter = await db.scalar(
        select(Chapter).where(
            Chapter.id == chapter_id,
            Chapter.project_id == project_id,
            Chapter.deleted_at.is_(None),
        )
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail={"code": "CHAPTER_NOT_FOUND"})
    body_revision = int(
        await db.scalar(select(ChapterBody.rev).where(ChapterBody.chapter_id == chapter_id)) or 0
    )
    rounds = list(
        (
            await db.execute(
                select(ChapterReviewRound)
                .where(
                    ChapterReviewRound.project_id == project_id,
                    ChapterReviewRound.chapter_id == chapter_id,
                )
                .order_by(
                    ChapterReviewRound.submitted_body_rev.desc(),
                    ChapterReviewRound.created_at.desc(),
                )
                .limit(30)
            )
        )
        .scalars()
        .all()
    )
    round_ids = [item.id for item in rounds]
    comments = (
        list(
            (
                await db.execute(
                    select(ReviewComment)
                    .where(ReviewComment.round_id.in_(round_ids))
                    .order_by(ReviewComment.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        if round_ids
        else []
    )
    user_ids = {
        value
        for round_ in rounds
        for value in (round_.submitted_by, round_.reviewed_by)
        if value
    } | {comment.author_id for comment in comments if comment.author_id}
    names = (
        dict((await db.execute(select(User.id, User.name).where(User.id.in_(user_ids)))).all())
        if user_ids
        else {}
    )
    by_round: dict[str, list[ReviewCommentOut]] = {round_id: [] for round_id in round_ids}
    for comment in comments:
        by_round[comment.round_id].append(
            ReviewCommentOut(
                id=comment.id,
                round_id=comment.round_id,
                chapter_id=comment.chapter_id,
                body_revision=comment.body_revision,
                paragraph_id=comment.paragraph_id,
                paragraph_excerpt=comment.paragraph_excerpt,
                selected_text=comment.selected_text,
                content=comment.content,
                status=comment.status,
                revision=comment.rev,
                author_id=comment.author_id,
                author_name=names.get(comment.author_id),
                resolved_by=comment.resolved_by,
                created_at=comment.created_at.isoformat(),
                resolved_at=comment.resolved_at.isoformat() if comment.resolved_at else None,
            )
        )
    return ReviewWorkspaceOut(
        chapter_id=chapter_id,
        current_body_revision=body_revision,
        can_submit=ProjectPermission.EDIT_BODY in permissions,
        can_review=ProjectPermission.REVIEW_CHAPTER in permissions,
        can_resolve=bool(
            {ProjectPermission.EDIT_BODY, ProjectPermission.REVIEW_CHAPTER} & permissions
        ),
        rounds=[
            ReviewRoundOut(
                id=round_.id,
                chapter_id=round_.chapter_id,
                submitted_body_revision=round_.submitted_body_rev,
                status=round_.status,
                submit_note=round_.submit_note,
                decision_note=round_.decision_note,
                revision=round_.rev,
                submitted_by=round_.submitted_by,
                submitted_by_name=names.get(round_.submitted_by),
                reviewed_by=round_.reviewed_by,
                reviewed_by_name=names.get(round_.reviewed_by),
                submitted_at=round_.submitted_at.isoformat(),
                reviewed_at=round_.reviewed_at.isoformat() if round_.reviewed_at else None,
                stale=round_.submitted_body_rev < body_revision,
                comments=by_round[round_.id],
            )
            for round_ in rounds
        ],
    )


@router.get(
    "/projects/{project_id}/chapters/{chapter_id}", response_model=ReviewWorkspaceOut
)
async def get_chapter_reviews(
    project_id: str,
    chapter_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReviewWorkspaceOut:
    permissions = await _require(project_id, ProjectPermission.VIEW, user, db)
    return await _serialize_workspace(project_id, chapter_id, permissions, db)


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/submit",
    response_model=ReviewWorkspaceOut,
    status_code=201,
)
async def submit_chapter_review(
    project_id: str,
    chapter_id: str,
    request: SubmitReviewRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReviewWorkspaceOut:
    permissions = await _require(project_id, ProjectPermission.EDIT_BODY, user, db)
    chapter = await db.scalar(
        select(Chapter).where(
            Chapter.id == chapter_id,
            Chapter.project_id == project_id,
            Chapter.deleted_at.is_(None),
        )
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail={"code": "CHAPTER_NOT_FOUND"})
    await verify_chapter_assignment(chapter, user, db)
    try:
        await submit_chapter(
            db,
            project_id=project_id,
            chapter_id=chapter_id,
            user_id=user.id,
            submit_note=request.submit_note,
        )
        await db.commit()
    except ReviewWorkflowError as error:
        await db.rollback()
        raise _workflow_http_error(error) from error
    return await _serialize_workspace(project_id, chapter_id, permissions, db)


@router.post(
    "/projects/{project_id}/rounds/{round_id}/comments",
    response_model=ReviewWorkspaceOut,
    status_code=201,
)
async def create_review_comment(
    project_id: str,
    round_id: str,
    request: CommentCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReviewWorkspaceOut:
    permissions = await _require(project_id, ProjectPermission.REVIEW_CHAPTER, user, db)
    try:
        comment = await add_comment(
            db,
            project_id=project_id,
            round_id=round_id,
            user_id=user.id,
            paragraph_id=request.paragraph_id,
            selected_text=request.selected_text,
            content=request.content,
        )
        chapter_id = comment.chapter_id
        await db.commit()
    except ReviewWorkflowError as error:
        await db.rollback()
        raise _workflow_http_error(error) from error
    return await _serialize_workspace(project_id, chapter_id, permissions, db)


@router.put(
    "/projects/{project_id}/rounds/{round_id}/comments/{comment_id}",
    response_model=ReviewWorkspaceOut,
)
async def edit_review_comment(
    project_id: str,
    round_id: str,
    comment_id: str,
    request: CommentUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReviewWorkspaceOut:
    permissions = await _require(project_id, ProjectPermission.REVIEW_CHAPTER, user, db)
    try:
        comment = await update_comment(
            db,
            project_id=project_id,
            round_id=round_id,
            comment_id=comment_id,
            expected_revision=request.expected_revision,
            content=request.content,
        )
        chapter_id = comment.chapter_id
        await db.commit()
    except ReviewWorkflowError as error:
        await db.rollback()
        raise _workflow_http_error(error) from error
    return await _serialize_workspace(project_id, chapter_id, permissions, db)


@router.post(
    "/projects/{project_id}/rounds/{round_id}/comments/{comment_id}/resolve",
    response_model=ReviewWorkspaceOut,
)
async def resolve_review_comment(
    project_id: str,
    round_id: str,
    comment_id: str,
    request: RevisionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReviewWorkspaceOut:
    permissions = await _permissions(project_id, user, db)
    if not ({ProjectPermission.EDIT_BODY, ProjectPermission.REVIEW_CHAPTER} & permissions):
        raise HTTPException(status_code=403, detail="Access denied")
    try:
        comment = await resolve_comment(
            db,
            project_id=project_id,
            round_id=round_id,
            comment_id=comment_id,
            expected_revision=request.expected_revision,
            user_id=user.id,
        )
        chapter_id = comment.chapter_id
        await db.commit()
    except ReviewWorkflowError as error:
        await db.rollback()
        raise _workflow_http_error(error) from error
    return await _serialize_workspace(project_id, chapter_id, permissions, db)


@router.post(
    "/projects/{project_id}/rounds/{round_id}/decision",
    response_model=ReviewWorkspaceOut,
)
async def review_decision(
    project_id: str,
    round_id: str,
    request: ReviewDecisionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReviewWorkspaceOut:
    permissions = await _require(project_id, ProjectPermission.REVIEW_CHAPTER, user, db)
    try:
        round_ = await decide_round(
            db,
            project_id=project_id,
            round_id=round_id,
            expected_revision=request.expected_revision,
            user_id=user.id,
            decision=request.decision,
            decision_note=request.decision_note,
        )
        chapter_id = round_.chapter_id
        await db.commit()
    except ReviewWorkflowError as error:
        await db.rollback()
        raise _workflow_http_error(error) from error
    return await _serialize_workspace(project_id, chapter_id, permissions, db)
