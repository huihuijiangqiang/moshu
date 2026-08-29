"""
章节 API
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import Chapter, ChapterBody
from db.session import get_db

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
async def get_chapter(chapter_id: str, db: AsyncSession = Depends(get_db)):
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
async def save_chapter_body(
    chapter_id: str,
    request: SaveChapterRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    保存章节正文 - 带乐观锁
    对应前端 mock: saveChapter

    关键约束：
    1. 请求带 base_rev，若服务端 rev 更高返回 409 + 双方内容
    2. 永不静默覆盖
    3. 保存成功后异步触发：守卫扫描 + codex_refs 重建

    Returns:
        200: 保存成功，返回新 rev
        409: 冲突，返回双方内容供用户选择
    """
    # 查询当前章节和正文
    stmt = select(Chapter).where(Chapter.id == chapter_id)
    result = await db.execute(stmt)
    chapter = result.scalar_one_or_none()

    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")

    body_stmt = select(ChapterBody).where(ChapterBody.chapter_id == chapter_id)
    body_result = await db.execute(body_stmt)
    body = body_result.scalar_one_or_none()

    # 乐观锁检查
    current_rev = body.rev if body else 0

    if request.base_rev < current_rev:
        # 冲突：返回 409 + 双方内容
        return ConflictResponse(
            server_content_html=body.content_html,
            server_content_json=body.content_json,
            server_rev=body.rev,
            client_content_html=request.content_html,
            client_content_json=request.content_json,
        )

    # 保存或更新正文
    new_rev = current_rev + 1

    if body:
        body.content_html = request.content_html
        body.content_json = request.content_json
        body.rev = new_rev
    else:
        body = ChapterBody(
            chapter_id=chapter_id,
            content_html=request.content_html,
            content_json=request.content_json,
            rev=new_rev,
        )
        db.add(body)

    # 更新章节字数（简化计算，实际应该从 content_json 精确统计）
    chapter.words = len(request.content_html)

    await db.commit()

    # TODO: 异步任务
    # 1. 同步重建 codex_refs（从 content_json 扫描 CodexRef 节点）
    # 2. 异步投递 Celery: guard_scan(chapter_id)

    return SaveChapterResponse(
        success=True,
        rev=new_rev,
        message="保存成功",
    )
