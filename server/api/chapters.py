"""
章节 API - 使用真实的 body save service
"""
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import ProjectPermission, get_current_user, verify_project_permission
from db import Chapter, ChapterBody
from db.models_core import User
from db.session import get_db
from services.body import (
    BodyRevisionConflictError,
    IdempotencyInProgressError,
    InvalidCodexRefError,
    save_chapter_body,
)
from services.idempotency import IdempotencyConflictError

router = APIRouter()


# Pydantic 模型
class ChapterOut(BaseModel):
    """章节详情 - 含正文"""

    id: str
    project_id: str
    volume_id: str | None
    title: str
    idx: int
    words: int
    outline: list[str]
    summary: str | None
    content_html: str
    content_json: dict
    rev: int  # 乐观锁版本号
    updated_at: str

    class Config:
        from_attributes = True


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
    stmt = select(Chapter).where(Chapter.id == chapter_id)
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
        content_html=body.content_html,
        content_json=body.content_json,
        rev=body.rev,
        updated_at=chapter.updated_at.isoformat(),
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
    stmt = select(Chapter).where(Chapter.id == chapter_id)
    result = await db.execute(stmt)
    chapter = result.scalar_one_or_none()

    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")

    # Verify project access
    await verify_project_permission(chapter.project_id, ProjectPermission.EDIT_BODY, user, db)

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
