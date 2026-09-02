"""
项目（作品）API
"""
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import (
    ProjectPermission,
    get_current_user,
    get_project_permissions,
    verify_project_access,
    verify_project_permission,
)
from db import Chapter, Project, Volume
from db.models_codex import CodexEntry
from db.models_consistency import ChapterOutlineState
from db.models_core import User
from db.models_guard import GuardIssue
from db.models_org import OrgMember
from db.session import get_db
from services.codex import create_entry

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
    inspiration: str | None
    synopsis: str | None
    story_settings: dict
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
    outline_note: str = ""
    outline_revision: int = 0
    outline_updated_at: str | None = None
    body_needs_revision: bool = False
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


class VolumeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=20_000)


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    genre: str | None = Field(default=None, max_length=100)
    target_words_daily: int = Field(default=3000, ge=100, le=100_000)
    inspiration: str = Field(default="", max_length=20_000)
    synopsis: str = Field(default="", max_length=50_000)
    protagonist: str = Field(default="", max_length=20_000)
    core_hook: str = Field(default="", max_length=20_000)
    audience: str = Field(default="", max_length=50)
    template: str = Field(default="", max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=20)
    volumes: list[VolumeCreate] = Field(default_factory=list, max_length=20)


class ChapterCreate(BaseModel):
    volume_id: str = Field(min_length=1, max_length=32)
    after_index: int = Field(ge=0)


async def _project_out(db: AsyncSession, project: Project) -> ProjectOut:
    volumes_result = await db.execute(
        select(Volume).where(Volume.project_id == project.id).order_by(Volume.idx)
    )
    return ProjectOut(
        id=project.id,
        title=project.title,
        genre=project.genre,
        status=project.status,
        target_words_daily=project.target_words_daily,
        style_profile_id=project.style_profile_id,
        inspiration=project.inspiration,
        synopsis=project.synopsis,
        story_settings=project.story_settings or {},
        created_at=project.created_at.isoformat(),
        volumes=[
            VolumeOut(id=volume.id, title=volume.title, idx=volume.idx, summary=volume.summary)
            for volume in volumes_result.scalars().all()
        ],
    )


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


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(
    request: ProjectCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    """Create a real project, its volume plan, first chapter, and initial codex entries."""
    project = Project(
        id=f"p_{secrets.token_hex(12)}",
        owner_id=user.id,
        title=request.title.strip(),
        genre=request.genre.strip() if request.genre else None,
        status="ongoing",
        target_words_daily=request.target_words_daily,
        inspiration=request.inspiration.strip() or None,
        synopsis=request.synopsis.strip() or None,
        story_settings={
            "audience": request.audience.strip(),
            "template": request.template.strip(),
            "tags": [tag.strip() for tag in request.tags if tag.strip()],
            "protagonist": request.protagonist.strip(),
            "core_hook": request.core_hook.strip(),
        },
    )
    db.add(project)
    await db.flush()

    volume_drafts = request.volumes or [VolumeCreate(title="第一卷 · 开篇")]
    volumes: list[Volume] = []
    for index, draft in enumerate(volume_drafts, start=1):
        volume = Volume(
            id=f"v_{secrets.token_hex(12)}",
            project_id=project.id,
            title=draft.title.strip(),
            idx=index * 1024,
            summary=draft.summary.strip() or None,
        )
        db.add(volume)
        volumes.append(volume)
    await db.flush()

    outline = [value for value in (request.inspiration.strip(), request.synopsis.strip()) if value]
    chapter = Chapter(
        id=f"ch_{secrets.token_hex(12)}",
        project_id=project.id,
        volume_id=volumes[0].id,
        title="第 1 章 · 开篇",
        idx=1,
        words=0,
        outline=outline,
    )
    db.add(chapter)

    protagonist = request.protagonist.strip()
    if protagonist:
        first_line = protagonist.splitlines()[0]
        name = first_line.split("·", 1)[0].strip() or "主角"
        await create_entry(
            db,
            project_id=project.id,
            kind="character",
            name=name,
            description=protagonist,
            resident=True,
            status="confirmed",
        )
    core_hook = request.core_hook.strip()
    if core_hook:
        name = core_hook.splitlines()[0].split("·", 1)[0].strip() or "核心规则"
        await create_entry(
            db,
            project_id=project.id,
            kind="rule",
            name=name,
            description=core_hook,
            resident=True,
            status="confirmed",
        )

    await db.commit()
    return await _project_out(db, project)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: str,
    project: Project = Depends(verify_project_access),
    db: AsyncSession = Depends(get_db),
):
    """
    获取项目详情
    对应前端 mock: getProject
    """
    return await _project_out(db, project)


@router.get("/{project_id}/permissions", response_model=list[str])
async def list_project_permissions(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[str]:
    _project, permissions = await get_project_permissions(project_id, user, db)
    if not permissions:
        raise HTTPException(status_code=403, detail="Access denied")
    return sorted(permission.value for permission in permissions)


@router.get("/{project_id}/chapters", response_model=list[ChapterListItem])
async def list_chapters(
    project_id: str,
    _project: Project = Depends(verify_project_access),
    db: AsyncSession = Depends(get_db),
):
    """
    获取章节列表 - 响应绝不含正文
    对应前端 mock: listChapters

    验收标准：80万字作品（约270章）这个响应应在 100KB 以内
    """
    stmt = (
        select(Chapter, ChapterOutlineState)
        .outerjoin(ChapterOutlineState, ChapterOutlineState.chapter_id == Chapter.id)
        .where(Chapter.project_id == project_id)
        .order_by(Chapter.idx)
    )
    result = await db.execute(stmt)
    chapters = result.all()

    return [
        ChapterListItem(
            id=ch.id,
            volume_id=ch.volume_id,
            title=ch.title,
            idx=ch.idx,
            words=ch.words,
            outline=ch.outline,
            summary=ch.summary,
            outline_note=state.note if state else "",
            outline_revision=state.revision if state else 0,
            outline_updated_at=state.updated_at.isoformat() if state and state.updated_at else None,
            body_needs_revision=state.body_needs_revision if state else False,
            updated_at=ch.updated_at.isoformat(),
        )
        for ch, state in chapters
    ]


@router.post("/{project_id}/chapters", response_model=ChapterListItem, status_code=201)
async def create_chapter(
    project_id: str,
    request: ChapterCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChapterListItem:
    """Insert a chapter and atomically keep book-wide chapter indexes contiguous."""
    await verify_project_permission(project_id, ProjectPermission.MANAGE_OUTLINE, user, db)
    volume_result = await db.execute(
        select(Volume)
        .where(Volume.id == request.volume_id, Volume.project_id == project_id)
        .with_for_update()
    )
    volume = volume_result.scalar_one_or_none()
    if volume is None:
        raise HTTPException(status_code=422, detail={"code": "VOLUME_NOT_IN_PROJECT"})

    chapter_result = await db.execute(
        select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.idx).with_for_update()
    )
    chapters = list(chapter_result.scalars().all())
    if request.after_index > 0 and not any(chapter.idx == request.after_index for chapter in chapters):
        raise HTTPException(status_code=422, detail={"code": "AFTER_CHAPTER_NOT_FOUND"})

    await db.execute(
        update(Chapter)
        .where(Chapter.project_id == project_id, Chapter.idx > request.after_index)
        .values(idx=Chapter.idx + 1)
    )
    new_index = request.after_index + 1
    chapter = Chapter(
        id=f"ch_{secrets.token_hex(12)}",
        project_id=project_id,
        volume_id=volume.id,
        title=f"第 {new_index} 章 · 待规划",
        idx=new_index,
        words=0,
        outline=[],
    )
    db.add(chapter)
    await db.commit()
    return ChapterListItem(
        id=chapter.id,
        volume_id=chapter.volume_id,
        title=chapter.title,
        idx=chapter.idx,
        words=0,
        outline=[],
        summary=None,
        updated_at=chapter.updated_at.isoformat(),
    )
