"""
项目（作品）API
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import Chapter, Project, Volume
from db.session import get_db

router = APIRouter()


# Pydantic 模型
class VolumeOut(BaseModel):
    id: str
    title: str
    idx: int
    summary: str | None

    class Config:
        from_attributes = True


class ProjectOut(BaseModel):
    id: str
    title: str
    genre: str | None
    status: str
    target_words_daily: int
    style_profile_id: str | None
    created_at: str
    volumes: list[VolumeOut]

    class Config:
        from_attributes = True


class ChapterListItem(BaseModel):
    """章节列表项 - 不含正文"""

    id: str
    volume_id: str | None
    title: str
    idx: int
    words: int
    outline: list[str]
    summary: str | None
    updated_at: str

    class Config:
        from_attributes = True


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str, db: AsyncSession = Depends(get_db)):
    """
    获取项目详情
    对应前端 mock: getProject
    """
    stmt = select(Project).where(Project.id == project_id)
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 加载卷列表
    volumes_stmt = select(Volume).where(Volume.project_id == project_id).order_by(Volume.idx)
    volumes_result = await db.execute(volumes_stmt)
    volumes = volumes_result.scalars().all()

    return ProjectOut(
        id=project.id,
        title=project.title,
        genre=project.genre,
        status=project.status,
        target_words_daily=project.target_words_daily,
        style_profile_id=project.style_profile_id,
        created_at=project.created_at.isoformat(),
        volumes=[
            VolumeOut(id=v.id, title=v.title, idx=v.idx, summary=v.summary)
            for v in volumes
        ],
    )


@router.get("/{project_id}/chapters", response_model=list[ChapterListItem])
async def list_chapters(project_id: str, db: AsyncSession = Depends(get_db)):
    """
    获取章节列表 - 响应绝不含正文
    对应前端 mock: listChapters

    验收标准：80万字作品（约270章）这个响应应在 100KB 以内
    """
    stmt = (
        select(Chapter)
        .where(Chapter.project_id == project_id)
        .order_by(Chapter.idx)
    )
    result = await db.execute(stmt)
    chapters = result.scalars().all()

    return [
        ChapterListItem(
            id=ch.id,
            volume_id=ch.volume_id,
            title=ch.title,
            idx=ch.idx,
            words=ch.words,
            outline=ch.outline,
            summary=ch.summary,
            updated_at=ch.updated_at.isoformat(),
        )
        for ch in chapters
    ]
