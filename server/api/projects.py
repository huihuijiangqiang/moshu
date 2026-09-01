"""
项目（作品）API
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from db import Chapter, Project, Volume
from db.models_codex import CodexEntry
from db.models_core import User
from db.models_guard import GuardIssue
from db.models_org import OrgMember
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


class ProjectListItem(BaseModel):
    id: str
    title: str
    genre: str | None
    status: str
    words: int
    chapters: int
    codex_count: int
    guard_open: int
    last_chapter_title: str | None
    updated_at: str
    target_words_daily: int


@router.get("", response_model=list[ProjectListItem])
async def list_projects(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出当前用户可访问的真实作品及书架摘要。"""
    org_ids = select(OrgMember.org_id).where(OrgMember.user_id == user.id)
    chapter_count = (
        select(func.count(Chapter.id))
        .where(Chapter.project_id == Project.id)
        .correlate(Project)
        .scalar_subquery()
    )
    word_count = (
        select(func.coalesce(func.sum(Chapter.words), 0))
        .where(Chapter.project_id == Project.id)
        .correlate(Project)
        .scalar_subquery()
    )
    codex_count = (
        select(func.count(CodexEntry.id))
        .where(CodexEntry.project_id == Project.id)
        .correlate(Project)
        .scalar_subquery()
    )
    guard_open = (
        select(func.count(GuardIssue.id))
        .where(GuardIssue.project_id == Project.id, GuardIssue.status == "open")
        .correlate(Project)
        .scalar_subquery()
    )
    latest_chapter_title = (
        select(Chapter.title)
        .where(Chapter.project_id == Project.id)
        .order_by(Chapter.updated_at.desc(), Chapter.idx.desc())
        .limit(1)
        .correlate(Project)
        .scalar_subquery()
    )
    latest_chapter_updated = (
        select(func.max(Chapter.updated_at))
        .where(Chapter.project_id == Project.id)
        .correlate(Project)
        .scalar_subquery()
    )

    rows = (
        await db.execute(
            select(
                Project,
                word_count.label("words"),
                chapter_count.label("chapters"),
                codex_count.label("codex_count"),
                guard_open.label("guard_open"),
                latest_chapter_title.label("last_chapter_title"),
                latest_chapter_updated.label("last_chapter_updated"),
            )
            .where(
                or_(
                    Project.owner_id == user.id,
                    Project.org_id.in_(org_ids),
                )
            )
            .order_by(Project.updated_at.desc(), Project.id)
        )
    ).all()

    return [
        ProjectListItem(
            id=project.id,
            title=project.title,
            genre=project.genre,
            status=project.status,
            words=int(words),
            chapters=int(chapters),
            codex_count=int(entry_count),
            guard_open=int(issue_count),
            last_chapter_title=last_title,
            updated_at=max(
                project.updated_at, chapter_updated or project.updated_at
            ).isoformat(),
            target_words_daily=project.target_words_daily,
        )
        for (
            project,
            words,
            chapters,
            entry_count,
            issue_count,
            last_title,
            chapter_updated,
        ) in rows
    ]


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
