"""Transactional chapter review workflow."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_core import Chapter, ChapterBody, ChapterVersion
from db.models_review import ChapterReviewRound, ReviewComment


class ReviewWorkflowError(RuntimeError):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


class ReviewRevisionConflictError(ReviewWorkflowError):
    def __init__(self, current_revision: int):
        self.current_revision = current_revision
        super().__init__("REVIEW_REVISION_CONFLICT")


def _text_content(node: object) -> str:
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return str(node.get("text") or "")
    return "".join(_text_content(child) for child in node.get("content", []) if isinstance(child, dict))


def paragraph_text(content_json: dict, paragraph_id: str) -> str | None:
    """Return the exact text for a locatable ProseMirror block."""

    def walk(nodes: object) -> str | None:
        if not isinstance(nodes, list):
            return None
        for node in nodes:
            if not isinstance(node, dict):
                continue
            attrs = node.get("attrs")
            if (
                node.get("type") in {"paragraph", "heading", "listItem"}
                and isinstance(attrs, dict)
                and attrs.get("pid") == paragraph_id
            ):
                return _text_content(node)
            nested = walk(node.get("content"))
            if nested is not None:
                return nested
        return None

    return walk(content_json.get("content"))


async def _locked_chapter(db: AsyncSession, project_id: str, chapter_id: str) -> Chapter:
    chapter = await db.scalar(
        select(Chapter)
        .where(
            Chapter.id == chapter_id,
            Chapter.project_id == project_id,
            Chapter.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if chapter is None:
        raise ReviewWorkflowError("CHAPTER_NOT_FOUND")
    return chapter


async def _locked_round(
    db: AsyncSession, project_id: str, round_id: str
) -> ChapterReviewRound:
    round_ = await db.scalar(
        select(ChapterReviewRound)
        .where(
            ChapterReviewRound.id == round_id,
            ChapterReviewRound.project_id == project_id,
        )
        .with_for_update()
    )
    if round_ is None:
        raise ReviewWorkflowError("REVIEW_ROUND_NOT_FOUND")
    return round_


async def submit_chapter(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    user_id: str,
    submit_note: str | None,
) -> ChapterReviewRound:
    await _locked_chapter(db, project_id, chapter_id)
    body = await db.scalar(
        select(ChapterBody).where(ChapterBody.chapter_id == chapter_id).with_for_update()
    )
    if body is None:
        raise ReviewWorkflowError("CHAPTER_BODY_REQUIRED")
    version = await db.scalar(
        select(ChapterVersion)
        .where(ChapterVersion.chapter_id == chapter_id, ChapterVersion.rev == body.rev)
        .order_by(ChapterVersion.id.desc())
        .limit(1)
    )
    if version is None:
        raise ReviewWorkflowError("CHAPTER_VERSION_REQUIRED")

    latest = await db.scalar(
        select(ChapterReviewRound)
        .where(ChapterReviewRound.chapter_id == chapter_id)
        .order_by(ChapterReviewRound.submitted_body_rev.desc(), ChapterReviewRound.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if latest is not None and body.rev <= latest.submitted_body_rev:
        raise ReviewWorkflowError("NEW_BODY_REVISION_REQUIRED")

    active = await db.scalar(
        select(ChapterReviewRound)
        .where(
            ChapterReviewRound.chapter_id == chapter_id,
            ChapterReviewRound.status == "submitted",
        )
        .with_for_update()
    )
    if active is not None:
        active.status = "superseded"
        active.rev += 1

    note = (submit_note or "").strip() or None
    round_ = ChapterReviewRound(
        id=uuid4().hex,
        project_id=project_id,
        chapter_id=chapter_id,
        submitted_body_rev=body.rev,
        status="submitted",
        submit_note=note,
        rev=1,
        submitted_by=user_id,
        submitted_at=datetime.now(UTC),
    )
    db.add(round_)
    await db.flush()
    return round_


async def add_comment(
    db: AsyncSession,
    *,
    project_id: str,
    round_id: str,
    user_id: str,
    paragraph_id: str,
    selected_text: str | None,
    content: str,
) -> ReviewComment:
    round_ = await _locked_round(db, project_id, round_id)
    if round_.status != "submitted":
        raise ReviewWorkflowError("REVIEW_ROUND_CLOSED")
    current_body_revision = await db.scalar(
        select(ChapterBody.rev).where(ChapterBody.chapter_id == round_.chapter_id)
    )
    if current_body_revision != round_.submitted_body_rev:
        raise ReviewWorkflowError("REVIEW_ROUND_STALE")
    version = await db.scalar(
        select(ChapterVersion)
        .where(
            ChapterVersion.chapter_id == round_.chapter_id,
            ChapterVersion.rev == round_.submitted_body_rev,
        )
        .order_by(ChapterVersion.id.desc())
        .limit(1)
    )
    if version is None:
        raise ReviewWorkflowError("CHAPTER_VERSION_REQUIRED")

    pid = paragraph_id.strip()
    block_text = paragraph_text(version.content_json, pid)
    if block_text is None:
        raise ReviewWorkflowError("PARAGRAPH_NOT_IN_SUBMITTED_VERSION")
    selection = (selected_text or "").strip() or None
    if selection is not None and selection not in block_text:
        raise ReviewWorkflowError("SELECTED_TEXT_NOT_IN_PARAGRAPH")
    comment_text = content.strip()
    if not comment_text:
        raise ReviewWorkflowError("COMMENT_REQUIRED")

    comment = ReviewComment(
        id=uuid4().hex,
        project_id=project_id,
        round_id=round_.id,
        chapter_id=round_.chapter_id,
        body_revision=round_.submitted_body_rev,
        paragraph_id=pid,
        paragraph_excerpt=block_text[:500],
        selected_text=selection,
        content=comment_text,
        status="open",
        rev=1,
        author_id=user_id,
    )
    db.add(comment)
    await db.flush()
    return comment


async def update_comment(
    db: AsyncSession,
    *,
    project_id: str,
    round_id: str,
    comment_id: str,
    expected_revision: int,
    content: str,
) -> ReviewComment:
    round_ = await _locked_round(db, project_id, round_id)
    if round_.status != "submitted":
        raise ReviewWorkflowError("REVIEW_ROUND_CLOSED")
    comment = await db.scalar(
        select(ReviewComment)
        .where(
            ReviewComment.id == comment_id,
            ReviewComment.round_id == round_.id,
            ReviewComment.project_id == project_id,
        )
        .with_for_update()
    )
    if comment is None:
        raise ReviewWorkflowError("REVIEW_COMMENT_NOT_FOUND")
    if comment.rev != expected_revision:
        raise ReviewRevisionConflictError(comment.rev)
    if comment.status != "open":
        raise ReviewWorkflowError("REVIEW_COMMENT_RESOLVED")
    comment_text = content.strip()
    if not comment_text:
        raise ReviewWorkflowError("COMMENT_REQUIRED")
    comment.content = comment_text
    comment.rev += 1
    await db.flush()
    return comment


async def resolve_comment(
    db: AsyncSession,
    *,
    project_id: str,
    round_id: str,
    comment_id: str,
    expected_revision: int,
    user_id: str,
) -> ReviewComment:
    round_ = await _locked_round(db, project_id, round_id)
    comment = await db.scalar(
        select(ReviewComment)
        .where(
            ReviewComment.id == comment_id,
            ReviewComment.round_id == round_.id,
            ReviewComment.project_id == project_id,
        )
        .with_for_update()
    )
    if comment is None:
        raise ReviewWorkflowError("REVIEW_COMMENT_NOT_FOUND")
    if comment.rev != expected_revision:
        raise ReviewRevisionConflictError(comment.rev)
    if comment.status == "resolved":
        return comment
    comment.status = "resolved"
    comment.resolved_by = user_id
    comment.resolved_at = datetime.now(UTC)
    comment.rev += 1
    await db.flush()
    return comment


async def decide_round(
    db: AsyncSession,
    *,
    project_id: str,
    round_id: str,
    expected_revision: int,
    user_id: str,
    decision: str,
    decision_note: str | None,
) -> ChapterReviewRound:
    round_ = await _locked_round(db, project_id, round_id)
    if round_.rev != expected_revision:
        raise ReviewRevisionConflictError(round_.rev)
    if round_.status != "submitted":
        raise ReviewWorkflowError("REVIEW_ROUND_CLOSED")
    current_body_revision = await db.scalar(
        select(ChapterBody.rev).where(ChapterBody.chapter_id == round_.chapter_id)
    )
    if current_body_revision != round_.submitted_body_rev:
        raise ReviewWorkflowError("REVIEW_ROUND_STALE")
    if decision not in {"approved", "changes_requested"}:
        raise ReviewWorkflowError("INVALID_REVIEW_DECISION")

    open_comments = int(
        await db.scalar(
            select(func.count())
            .select_from(ReviewComment)
            .where(ReviewComment.round_id == round_.id, ReviewComment.status == "open")
        )
        or 0
    )
    note = (decision_note or "").strip() or None
    if decision == "approved" and open_comments:
        raise ReviewWorkflowError("OPEN_COMMENTS_PREVENT_APPROVAL")
    if decision == "changes_requested" and not open_comments and note is None:
        raise ReviewWorkflowError("CHANGES_REQUEST_REASON_REQUIRED")

    round_.status = decision
    round_.decision_note = note
    round_.reviewed_by = user_id
    round_.reviewed_at = datetime.now(UTC)
    round_.rev += 1
    await db.flush()
    return round_
