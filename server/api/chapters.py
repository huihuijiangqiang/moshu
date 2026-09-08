"""
章节 API - 使用真实的 body save service
"""
import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import (
    ProjectPermission,
    get_current_user,
    verify_chapter_assignment,
    verify_project_permission,
)
from db import Chapter, ChapterBody, ChapterVersion
from db.models_core import User
from db.session import get_db
from services.body import (
    BodyRevisionConflictError,
    IdempotencyInProgressError,
    InvalidCodexRefError,
    count_words,
    save_chapter_body,
)
from services.character_tracking import (
    CharacterStatisticsNotFoundError,
    InvalidPovEntryError,
    PovRevisionConflictError,
    update_chapter_pov,
)
from services.chapter_chunks import count_current_chapter_chunks, embed_pending_chapter_chunks
from services.embedding import EmbeddingProviderError, GatewayEmbeddingProvider
from services.idempotency import IdempotencyConflictError
from services.retrieval import ConsistencyRetrieval

router = APIRouter()


# Pydantic 模型
class ChapterOut(BaseModel):
    """章节详情 - 含正文"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    volume_id: str | None
    title: str
    idx: int
    words: int
    outline: list[str]
    summary: str | None
    temporal_anchor: dict | None
    pov_entry_id: str | None
    pov_revision: int
    content_html: str
    content_json: dict
    rev: int  # 乐观锁版本号
    updated_at: str

class SaveChapterRequest(BaseModel):
    """保存章节请求"""

    content_html: str
    content_json: dict
    base_rev: int  # 客户端持有的版本号


class SaveChapterResponse(BaseModel):
    """保存章节响应"""

    success: bool
    rev: int
    consistency_status: str
    message: str | None = None


class ConflictResponse(BaseModel):
    """冲突响应 - 返回双方内容"""

    conflict: bool = True
    server_content_html: str
    server_content_json: dict
    server_rev: int
    client_content_html: str
    client_content_json: dict


class ChapterVersionSummary(BaseModel):
    id: int
    rev: int
    trigger: str
    words: int
    excerpt: str
    created_at: str
    is_current: bool


class ChapterVersionDetail(ChapterVersionSummary):
    content_html: str
    content_json: dict


class RestoreChapterVersionRequest(BaseModel):
    base_rev: int = Field(ge=0)


class RestoreChapterVersionResponse(BaseModel):
    rev: int
    restored_from_rev: int
    content_html: str
    content_json: dict
    words: int
    consistency_status: str


class ChapterPovRequest(BaseModel):
    entry_id: str | None = Field(default=None, max_length=32)
    expected_revision: int = Field(ge=0)


class ChapterPovResponse(BaseModel):
    chapter_id: str
    entry_id: str | None
    revision: int


class ChapterChunkSearchResponse(BaseModel):
    chunk_id: str
    chapter_id: str
    body_rev: int
    chunk_index: int
    paragraph_start: int
    paragraph_end: int
    paragraph_ids: list[str]
    content_text: str
    distance: float
    similarity: float


class ChapterChunkReindexResponse(BaseModel):
    chapter_id: str
    body_rev: int
    embedded: int
    counts: dict[str, int]


def _document_excerpt(content_json: dict, limit: int = 140) -> str:
    parts: list[str] = []

    def walk(nodes: object) -> None:
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            text = node.get("text")
            if isinstance(text, str):
                parts.append(text)
            walk(node.get("content"))

    walk(content_json.get("content"))
    text = "".join(parts).strip()
    if not text:
        return "空白正文"
    return f"{text[:limit]}…" if len(text) > limit else text


def _version_summary(version: ChapterVersion, current_rev: int) -> ChapterVersionSummary:
    return ChapterVersionSummary(
        id=version.id,
        rev=version.rev,
        trigger=version.trigger,
        words=count_words(version.content_json),
        excerpt=_document_excerpt(version.content_json),
        created_at=version.created_at.isoformat(),
        is_current=version.rev == current_rev,
    )


async def _active_chapter(chapter_id: str, db: AsyncSession) -> Chapter:
    chapter = await db.scalar(
        select(Chapter).where(Chapter.id == chapter_id, Chapter.deleted_at.is_(None))
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail={"code": "CHAPTER_NOT_FOUND"})
    return chapter


@router.get("/{chapter_id}", response_model=ChapterOut)
async def get_chapter(
    chapter_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    获取章节详情 - 含正文
    对应前端 mock: getChapter

    注意：rev 必须返回，前端离线冲突判定要用
    """
    stmt = select(Chapter).where(Chapter.id == chapter_id, Chapter.deleted_at.is_(None))
    result = await db.execute(stmt)
    chapter = result.scalar_one_or_none()

    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")

    # Verify project access
    await verify_project_permission(chapter.project_id, ProjectPermission.VIEW, user, db)

    # 加载正文
    body_stmt = select(ChapterBody).where(ChapterBody.chapter_id == chapter_id)
    body_result = await db.execute(body_stmt)
    body = body_result.scalar_one_or_none()

    if not body:
        # 新章节可能还没有正文
        return ChapterOut(
            id=chapter.id,
            project_id=chapter.project_id,
            volume_id=chapter.volume_id,
            title=chapter.title,
            idx=chapter.idx,
            words=chapter.words,
            outline=chapter.outline,
            summary=chapter.summary,
            temporal_anchor=chapter.temporal_anchor,
            pov_entry_id=chapter.pov_entry_id,
            pov_revision=chapter.pov_revision,
            content_html="",
            content_json={},
            rev=0,
            updated_at=chapter.updated_at.isoformat(),
        )

    return ChapterOut(
        id=chapter.id,
        project_id=chapter.project_id,
        volume_id=chapter.volume_id,
        title=chapter.title,
        idx=chapter.idx,
        words=chapter.words,
        outline=chapter.outline,
        summary=chapter.summary,
        temporal_anchor=chapter.temporal_anchor,
        pov_entry_id=chapter.pov_entry_id,
        pov_revision=chapter.pov_revision,
        content_html=body.content_html,
        content_json=body.content_json,
        rev=body.rev,
        updated_at=chapter.updated_at.isoformat(),
    )


@router.put("/{chapter_id}/pov", response_model=ChapterPovResponse)
async def set_chapter_pov(
    chapter_id: str,
    request: ChapterPovRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChapterPovResponse:
    chapter = await _active_chapter(chapter_id, db)
    await verify_project_permission(chapter.project_id, ProjectPermission.MANAGE_OUTLINE, user, db)
    await verify_chapter_assignment(chapter, user, db)
    try:
        chapter = await update_chapter_pov(
            db,
            chapter_id=chapter_id,
            entry_id=request.entry_id,
            expected_revision=request.expected_revision,
        )
        response = ChapterPovResponse(
            chapter_id=chapter.id,
            entry_id=chapter.pov_entry_id,
            revision=chapter.pov_revision,
        )
        await db.commit()
    except PovRevisionConflictError as error:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "POV_REVISION_CONFLICT",
                "current_entry_id": error.current_entry_id,
                "current_revision": error.current_revision,
            },
        ) from error
    except InvalidPovEntryError as error:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_POV_CHARACTER", "entry_id": str(error)},
        ) from error
    except CharacterStatisticsNotFoundError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail={"code": "CHAPTER_NOT_FOUND"}) from error

    return response


@router.get("/{chapter_id}/versions", response_model=list[ChapterVersionSummary])
async def list_chapter_versions(
    chapter_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ChapterVersionSummary]:
    """List lightweight immutable snapshots without loading every historical body."""
    chapter = await _active_chapter(chapter_id, db)
    await verify_project_permission(chapter.project_id, ProjectPermission.VIEW, user, db)
    current_rev = await db.scalar(
        select(ChapterBody.rev).where(ChapterBody.chapter_id == chapter_id)
    ) or 0
    versions = list(
        (
            await db.execute(
                select(ChapterVersion)
                .where(ChapterVersion.chapter_id == chapter_id)
                .order_by(ChapterVersion.rev.desc(), ChapterVersion.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [_version_summary(version, current_rev) for version in versions]


@router.get("/{chapter_id}/versions/{revision}", response_model=ChapterVersionDetail)
async def get_chapter_version(
    chapter_id: str,
    revision: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChapterVersionDetail:
    chapter = await _active_chapter(chapter_id, db)
    await verify_project_permission(chapter.project_id, ProjectPermission.VIEW, user, db)
    version = await db.scalar(
        select(ChapterVersion)
        .where(ChapterVersion.chapter_id == chapter_id, ChapterVersion.rev == revision)
        .order_by(ChapterVersion.id.desc())
        .limit(1)
    )
    if version is None:
        raise HTTPException(status_code=404, detail={"code": "CHAPTER_VERSION_NOT_FOUND"})
    current_rev = await db.scalar(
        select(ChapterBody.rev).where(ChapterBody.chapter_id == chapter_id)
    ) or 0
    summary = _version_summary(version, current_rev)
    return ChapterVersionDetail(
        **summary.model_dump(),
        content_html=version.content_html,
        content_json=version.content_json,
    )


@router.get("/{chapter_id}/chunks/search", response_model=list[ChapterChunkSearchResponse])
async def search_chapter_chunks(
    chapter_id: str,
    query: str = Query(..., min_length=1, max_length=2000),
    top_k: int = Query(default=8, ge=1, le=50),
    threshold: float = Query(default=0.55, ge=0.0, le=1.0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ChapterChunkSearchResponse]:
    """Search semantic chunks for one chapter's current body revision."""
    chapter = await _active_chapter(chapter_id, db)
    await verify_project_permission(chapter.project_id, ProjectPermission.VIEW, user, db)
    retrieval = ConsistencyRetrieval(embedding_provider=GatewayEmbeddingProvider())
    try:
        rows = await retrieval.retrieve_chapter_chunks_l3(
            db,
            chapter.project_id,
            query,
            top_k=top_k,
            threshold=threshold,
            chapter_ids=[chapter_id],
        )
    except (EmbeddingProviderError, httpx.HTTPError, ValueError) as error:
        raise HTTPException(status_code=503, detail="chapter chunk retrieval unavailable") from error
    return [ChapterChunkSearchResponse(**row) for row in rows]


@router.post("/{chapter_id}/chunks/reindex", response_model=ChapterChunkReindexResponse)
async def reindex_chapter_chunks(
    chapter_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChapterChunkReindexResponse:
    """Embed pending chunks for the current body revision on explicit request."""
    chapter = await _active_chapter(chapter_id, db)
    await verify_project_permission(chapter.project_id, ProjectPermission.EDIT_BODY, user, db)
    body_rev = int(await db.scalar(select(ChapterBody.rev).where(ChapterBody.chapter_id == chapter_id)) or 0)
    try:
        embedded = await embed_pending_chapter_chunks(
            db, GatewayEmbeddingProvider(), project_id=chapter.project_id, chapter_id=chapter_id
        )
    except (EmbeddingProviderError, httpx.HTTPError, ValueError) as error:
        await db.rollback()
        raise HTTPException(status_code=503, detail="chapter chunk embedding unavailable") from error
    counts = await count_current_chapter_chunks(db, chapter_id=chapter_id, body_rev=body_rev)
    await db.commit()
    return ChapterChunkReindexResponse(
        chapter_id=chapter_id, body_rev=body_rev, embedded=embedded, counts=counts
    )


@router.post("/{chapter_id}/versions/{revision}/restore", response_model=RestoreChapterVersionResponse)
async def restore_chapter_version(
    chapter_id: str,
    revision: int,
    request: RestoreChapterVersionRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RestoreChapterVersionResponse:
    """Restore a snapshot as a new head revision; never rewrite immutable history."""
    chapter = await _active_chapter(chapter_id, db)
    await verify_project_permission(chapter.project_id, ProjectPermission.EDIT_BODY, user, db)
    await verify_chapter_assignment(chapter, user, db)
    version = await db.scalar(
        select(ChapterVersion)
        .where(ChapterVersion.chapter_id == chapter_id, ChapterVersion.rev == revision)
        .order_by(ChapterVersion.id.desc())
        .limit(1)
    )
    if version is None:
        raise HTTPException(status_code=404, detail={"code": "CHAPTER_VERSION_NOT_FOUND"})

    version_html = version.content_html
    version_json = version.content_json
    body_stmt = select(ChapterBody).where(ChapterBody.chapter_id == chapter_id)
    try:
        result = await save_chapter_body(
            db,
            chapter_id=chapter_id,
            content_html=version_html,
            content_json=version_json,
            base_rev=request.base_rev,
            idempotency_key=idempotency_key,
            trigger="restore_version",
        )
        await db.commit()
    except BodyRevisionConflictError:
        await db.rollback()
        body = await db.scalar(body_stmt)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ConflictResponse(
                server_content_html=body.content_html if body else "",
                server_content_json=body.content_json if body else {},
                server_rev=body.rev if body else 0,
                client_content_html=version_html,
                client_content_json=version_json,
            ).model_dump(),
        )
    except InvalidCodexRefError as error:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        )
    except IdempotencyConflictError as error:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Idempotency key conflict: {error.key}",
        )
    except IdempotencyInProgressError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_202_ACCEPTED,
            detail="Request is being processed, please retry later",
        )
    except Exception as error:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(error),
        )

    return RestoreChapterVersionResponse(
        rev=result["rev"],
        restored_from_rev=revision,
        content_html=version_html,
        content_json=version_json,
        words=count_words(version_json),
        consistency_status=result["consistency_status"],
    )


@router.put("/{chapter_id}/body")
async def save_chapter_body_endpoint(
    chapter_id: str,
    request: SaveChapterRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    保存章节正文 - 带乐观锁和幂等性

    关键约束：
    1. 请求带 base_rev，若服务端 rev 更高返回 409 + 双方内容
    2. 永不静默覆盖
    3. 保存成功后异步触发：守卫扫描 + codex_refs 重建
    4. 必须提供 Idempotency-Key header

    Returns:
        200: 保存成功，返回新 rev 和 consistency_status
        202: 一致性检查进行中（幂等重放）
        409: 冲突，返回双方内容供用户选择
        422: 无效的 CodexRef（cross-project 或不存在）
    """
    # 查询当前章节和正文
    stmt = select(Chapter).where(Chapter.id == chapter_id, Chapter.deleted_at.is_(None))
    result = await db.execute(stmt)
    chapter = result.scalar_one_or_none()

    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")

    # Verify project access
    await verify_project_permission(chapter.project_id, ProjectPermission.EDIT_BODY, user, db)
    await verify_chapter_assignment(chapter, user, db)

    body_stmt = select(ChapterBody).where(ChapterBody.chapter_id == chapter_id)
    body_result = await db.execute(body_stmt)
    body = body_result.scalar_one_or_none()

    # 乐观锁检查
    current_rev = body.rev if body else 0

    if request.base_rev < current_rev:
        # 冲突：返回 409 + 双方内容
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ConflictResponse(
                server_content_html=body.content_html,
                server_content_json=body.content_json,
                server_rev=body.rev,
                client_content_html=request.content_html,
                client_content_json=request.content_json,
            ).model_dump(),
        )

    # Call the real save_chapter_body service
    try:
        result = await save_chapter_body(
            db,
            chapter_id=chapter_id,
            content_html=request.content_html,
            content_json=request.content_json,
            base_rev=request.base_rev,
            idempotency_key=idempotency_key,
            trigger="manual",
        )
        await db.commit()
    except InvalidCodexRefError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    except BodyRevisionConflictError:
        await db.rollback()
        # Re-fetch current body for conflict response
        body_result = await db.execute(body_stmt)
        body = body_result.scalar_one_or_none()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ConflictResponse(
                server_content_html=body.content_html if body else "",
                server_content_json=body.content_json if body else {},
                server_rev=body.rev if body else 0,
                client_content_html=request.content_html,
                client_content_json=request.content_json,
            ).model_dump(),
        )
    except IdempotencyConflictError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Idempotency key conflict: {e.key}",
        )
    except IdempotencyInProgressError:
        raise HTTPException(
            status_code=status.HTTP_202_ACCEPTED,
            detail="Request is being processed, please retry later",
        )
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )

    # Return appropriate status based on consistency_status
    if result["consistency_status"] == "queued":
        return SaveChapterResponse(
            success=True,
            rev=result["rev"],
            consistency_status="queued",
            message="保存成功，一致性检查已排队",
        )
    else:
        # consistency_status == "unchanged"
        return SaveChapterResponse(
            success=True,
            rev=result["rev"],
            consistency_status="unchanged",
            message="保存成功",
        )
