"""
项目（作品）API
"""
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import (
    ProjectPermission,
    get_current_user,
    get_project_permissions,
    verify_project_access,
    verify_project_permission,
)
from db import Chapter, Project, ProjectNote, ProjectPositioning, ProjectPositioningRevision, Volume
from db.models_codex import CodexEntry
from db.models_consistency import ChapterOutlineState
from db.models_core import User
from db.models_guard import GuardIssue
from db.models_org import OrgMember
from db.models_writing import ProjectDailyWriting
from db.session import get_db
from services.codex import create_entry
from services.provider_usage import record_account_platform_usage
from services.wizard_planning import (
    WizardChapterPlan,
    WizardPlanner,
    WizardPlanningError,
    WizardStoryPlan,
)

router = APIRouter()


# Pydantic 模型
class VolumeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    idx: int
    summary: str | None


class ProjectPositioningOut(BaseModel):
    """Platform promise card and its optimistic-lock revision."""

    id: str
    project_id: str
    platform: Literal["fanqie", "qimao", "qidian", "general"]
    title_candidates: list[str]
    selling_point: str
    synopsis: str
    tags: list[str]
    protagonist_dilemma: str
    first_payoff: str
    long_term_arc: str
    revision: int
    status: Literal["draft", "active", "archived"]
    created_at: str
    updated_at: str


class ProjectPositioningRevisionOut(ProjectPositioningOut):
    pass

class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    org_id: str | None
    title: str
    genre: str | None
    status: str
    target_words_daily: int
    today_words: int
    style_profile_id: str | None
    inspiration: str | None
    synopsis: str | None
    story_settings: dict
    created_at: str
    volumes: list[VolumeOut]
    target_platform: Literal["fanqie", "qimao", "qidian", "general"] = "general"
    positioning: ProjectPositioningOut | None = None

class ChapterListItem(BaseModel):
    """章节列表项 - 不含正文"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    volume_id: str | None
    title: str
    idx: int
    words: int
    outline: list[str]
    summary: str | None
    temporal_anchor: dict | None = None
    pov_entry_id: str | None = None
    pov_revision: int = 0
    outline_note: str = ""
    outline_revision: int = 0
    outline_updated_at: str | None = None
    body_needs_revision: bool = False
    updated_at: str

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
    today_words: int


class WritingProgressDayOut(BaseModel):
    """One UTC calendar day's net-positive writing progress."""

    date: str
    words_added: int
    saves: int
    target_words_daily: int
    target_met: bool


class VolumeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=20_000)


class ProjectChapterPlan(WizardChapterPlan):
    """章节初始计划，支持可选的 0 起始卷索引。

    ``WizardChapterPlan`` 用于模型规划响应，保持其严格结构不变；项目创建
    请求单独扩展 ``volumeIndex``，这样旧客户端（没有该字段）仍默认归第一卷。
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # Range validation depends on the number of volumes in this request and is
    # therefore performed by ``create_project`` rather than by Pydantic.
    volume_index: int | None = Field(default=None, alias="volumeIndex")


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
    chapters: list[ProjectChapterPlan] = Field(default_factory=list, max_length=10)
    target_platform: Literal["fanqie", "qimao", "qidian", "general"] = "general"


class WizardPlanRequest(BaseModel):
    inspiration: str = Field(min_length=8, max_length=2_000)
    audience: str = Field(min_length=1, max_length=50)
    genre: str = Field(min_length=1, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=3)
    template: str = Field(min_length=1, max_length=100)


class ChapterCreate(BaseModel):
    volume_id: str = Field(min_length=1, max_length=32)
    after_index: int = Field(ge=0)


class ProjectPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    genre: str | None = Field(default=None, max_length=100)
    status: Literal["ongoing", "finished", "archived"] | None = None
    target_words_daily: int | None = Field(default=None, ge=100, le=100_000)


class ProjectPositioningPatch(BaseModel):
    """Partial update; omitted fields remain unchanged."""

    expected_revision: int = Field(ge=0)
    platform: Literal["fanqie", "qimao", "qidian", "general"] | None = None
    title_candidates: list[str] | None = Field(default=None, max_length=20)
    selling_point: str | None = Field(default=None, max_length=20_000)
    synopsis: str | None = Field(default=None, max_length=50_000)
    tags: list[str] | None = Field(default=None, max_length=30)
    protagonist_dilemma: str | None = Field(default=None, max_length=20_000)
    first_payoff: str | None = Field(default=None, max_length=20_000)
    long_term_arc: str | None = Field(default=None, max_length=20_000)
    status: Literal["draft", "active", "archived"] | None = None


class ProjectPositioningRestoreRequest(BaseModel):
    expected_revision: int = Field(ge=0)


class VolumePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=20_000)


class VolumeOrderRequest(BaseModel):
    volume_ids: list[str] = Field(min_length=1, max_length=100)


class ChapterPositionRequest(BaseModel):
    volume_id: str = Field(min_length=1, max_length=32)
    placement: Literal["first", "last", "after"] = "last"
    after_chapter_id: str | None = Field(default=None, max_length=32)


class RestoreChapterRequest(BaseModel):
    volume_id: str | None = Field(default=None, max_length=32)


class TrashVolumeOut(BaseModel):
    id: str
    title: str
    deleted_at: str


class TrashChapterOut(BaseModel):
    id: str
    title: str
    words: int
    volume_id: str | None
    volume_title: str | None
    deleted_at: str


class ProjectTrashOut(BaseModel):
    volumes: list[TrashVolumeOut]
    chapters: list[TrashChapterOut]


class ProjectNoteCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2_000)
    chapter_id: str | None = Field(default=None, max_length=32)


class ProjectNoteOut(BaseModel):
    id: str
    project_id: str
    chapter_id: str | None
    chapter_index: int | None
    chapter_title: str | None
    content: str
    created_at: datetime


def _clean_required(value: str, code: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise HTTPException(status_code=422, detail={"code": code})
    return cleaned


def get_wizard_planner() -> WizardPlanner:
    return WizardPlanner()


async def _active_volumes(db: AsyncSession, project_id: str, *, lock: bool = False) -> list[Volume]:
    statement = (
        select(Volume)
        .where(Volume.project_id == project_id, Volume.deleted_at.is_(None))
        .order_by(Volume.idx, Volume.id)
    )
    if lock:
        statement = statement.with_for_update()
    return list((await db.execute(statement)).scalars().all())


async def _active_chapters(db: AsyncSession, project_id: str, *, lock: bool = False) -> list[Chapter]:
    statement = (
        select(Chapter)
        .where(Chapter.project_id == project_id, Chapter.deleted_at.is_(None))
        .order_by(Chapter.idx, Chapter.id)
    )
    if lock:
        statement = statement.with_for_update()
    return list((await db.execute(statement)).scalars().all())


def _renumber_volumes(volumes: list[Volume]) -> None:
    for index, volume in enumerate(volumes, start=1):
        volume.idx = index * 1024


def _renumber_chapters(chapters: list[Chapter]) -> None:
    for index, chapter in enumerate(chapters, start=1):
        chapter.idx = index


def _chapters_in_volume_order(chapters: list[Chapter], volumes: list[Volume]) -> list[Chapter]:
    """Keep chapters grouped by active volume; legacy unassigned chapters sort last."""
    order = {volume.id: index for index, volume in enumerate(volumes)}
    return sorted(chapters, key=lambda chapter: (order.get(chapter.volume_id, len(volumes)), chapter.idx, chapter.id))


def _touch_project(project: Project) -> None:
    project.updated_at = datetime.now(UTC)


def _volume_out(volume: Volume) -> VolumeOut:
    return VolumeOut(id=volume.id, title=volume.title, idx=volume.idx, summary=volume.summary)


async def _locked_active_volume(db: AsyncSession, project_id: str, volume_id: str) -> Volume:
    result = await db.execute(
        select(Volume)
        .where(
            Volume.id == volume_id,
            Volume.project_id == project_id,
            Volume.deleted_at.is_(None),
        )
        .with_for_update()
    )
    volume = result.scalar_one_or_none()
    if volume is None:
        raise HTTPException(status_code=422, detail={"code": "VOLUME_NOT_IN_PROJECT"})
    return volume


async def _lock_project(db: AsyncSession, project_id: str) -> None:
    """Serialize structural edits for one book before taking volume/chapter locks."""
    await db.execute(select(Project.id).where(Project.id == project_id).with_for_update())


def _chapter_list_item(chapter: Chapter, state: ChapterOutlineState | None = None) -> ChapterListItem:
    return ChapterListItem(
        id=chapter.id,
        volume_id=chapter.volume_id,
        title=chapter.title,
        idx=chapter.idx,
        words=chapter.words,
        outline=chapter.outline,
        summary=chapter.summary,
        temporal_anchor=chapter.temporal_anchor,
        pov_entry_id=chapter.pov_entry_id,
        pov_revision=chapter.pov_revision,
        outline_note=state.note if state else "",
        outline_revision=state.revision if state else 0,
        outline_updated_at=state.updated_at.isoformat() if state and state.updated_at else None,
        body_needs_revision=state.body_needs_revision if state else False,
        updated_at=chapter.updated_at.isoformat(),
    )


def _positioning_out(positioning: ProjectPositioning | ProjectPositioningRevision) -> ProjectPositioningOut:
    """Serialize current or historical positioning without exposing ORM state."""
    return ProjectPositioningOut(
        id=positioning.id,
        project_id=positioning.project_id,
        platform=positioning.platform,
        title_candidates=list(positioning.title_candidates or []),
        selling_point=positioning.selling_point,
        synopsis=positioning.synopsis,
        tags=list(positioning.tags or []),
        protagonist_dilemma=positioning.protagonist_dilemma,
        first_payoff=positioning.first_payoff,
        long_term_arc=positioning.long_term_arc,
        revision=positioning.revision,
        status=positioning.status,
        created_at=positioning.created_at.isoformat(),
        updated_at=(positioning.updated_at.isoformat() if hasattr(positioning, "updated_at") else positioning.created_at.isoformat()),
    )


async def _project_positioning(db: AsyncSession, project_id: str) -> ProjectPositioning | None:
    return await db.scalar(
        select(ProjectPositioning).where(ProjectPositioning.project_id == project_id)
    )


async def _project_out(db: AsyncSession, project: Project) -> ProjectOut:
    volumes_result = await db.execute(
        select(Volume)
        .where(Volume.project_id == project.id, Volume.deleted_at.is_(None))
        .order_by(Volume.idx)
    )
    today_words = await db.scalar(
        select(func.coalesce(func.sum(ProjectDailyWriting.words_added), 0)).where(
            ProjectDailyWriting.project_id == project.id,
            ProjectDailyWriting.day == datetime.now(UTC).date(),
        )
    )
    positioning = await _project_positioning(db, project.id)
    return ProjectOut(
        id=project.id,
        org_id=project.org_id,
        title=project.title,
        genre=project.genre,
        status=project.status,
        target_words_daily=project.target_words_daily,
        today_words=int(today_words or 0),
        style_profile_id=project.style_profile_id,
        inspiration=project.inspiration,
        synopsis=project.synopsis,
        story_settings=project.story_settings or {},
        created_at=project.created_at.isoformat(),
        volumes=[
            VolumeOut(id=volume.id, title=volume.title, idx=volume.idx, summary=volume.summary)
            for volume in volumes_result.scalars().all()
        ],
        target_platform=positioning.platform if positioning else "general",
        positioning=_positioning_out(positioning) if positioning else None,
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
        .where(Chapter.project_id == Project.id, Chapter.deleted_at.is_(None))
        .correlate(Project)
        .scalar_subquery()
    )
    word_count = (
        select(func.coalesce(func.sum(Chapter.words), 0))
        .where(Chapter.project_id == Project.id, Chapter.deleted_at.is_(None))
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
        .where(Chapter.project_id == Project.id, Chapter.deleted_at.is_(None))
        .order_by(Chapter.updated_at.desc(), Chapter.idx.desc())
        .limit(1)
        .correlate(Project)
        .scalar_subquery()
    )
    latest_chapter_updated = (
        select(func.max(Chapter.updated_at))
        .where(Chapter.project_id == Project.id, Chapter.deleted_at.is_(None))
        .correlate(Project)
        .scalar_subquery()
    )
    today_words = (
        select(func.coalesce(func.sum(ProjectDailyWriting.words_added), 0))
        .where(
            ProjectDailyWriting.project_id == Project.id,
            ProjectDailyWriting.day == datetime.now(UTC).date(),
        )
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
                today_words.label("today_words"),
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
            today_words=int(written_today),
        )
        for (
            project,
            words,
            chapters,
            entry_count,
            issue_count,
            last_title,
            chapter_updated,
            written_today,
        ) in rows
    ]


@router.post("/wizard/plan", response_model=WizardStoryPlan)
async def plan_project(
    request: WizardPlanRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    planner: WizardPlanner = Depends(get_wizard_planner),
) -> WizardStoryPlan:
    try:
        plan = await planner.plan(
            inspiration=request.inspiration.strip(),
            audience=request.audience.strip(),
            genre=request.genre.strip(),
            tags=[tag.strip() for tag in request.tags if tag.strip()],
            template=request.template.strip(),
        )
    except WizardPlanningError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "WIZARD_PLANNING_FAILED", "message": "故事骨架生成失败，请重试。"},
        ) from exc
    await record_account_platform_usage(
        db,
        user_id=user.id,
        feature="wizard_plan",
        events=planner.usage_events,
    )
    await db.commit()
    return plan


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(
    request: ProjectCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    """Create a real project, its volume plan, first chapter, and initial codex entries."""
    volume_count = len(request.volumes) or 1
    for chapter_index, chapter_plan in enumerate(request.chapters):
        volume_index = chapter_plan.volume_index
        if volume_index is not None and not 0 <= volume_index < volume_count:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "CHAPTER_VOLUME_INDEX_OUT_OF_RANGE",
                    "field": f"chapters[{chapter_index}].volumeIndex",
                    "volume_index": volume_index,
                    "volume_count": volume_count,
                    "message": f"volumeIndex 必须是 0 到 {volume_count - 1} 之间的整数。",
                },
            )

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
    db.add(
        ProjectPositioning(
            id=f"pp_{secrets.token_hex(12)}",
            project_id=project.id,
            platform=request.target_platform,
            synopsis=request.synopsis.strip(),
            tags=[tag.strip() for tag in request.tags if tag.strip()],
            protagonist_dilemma=request.inspiration.strip(),
            selling_point=request.core_hook.strip(),
        )
    )
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

    default_outline = [
        value for value in (request.inspiration.strip(), request.synopsis.strip()) if value
    ]
    if len(default_outline) < 2:
        default_outline.extend(
            ["建立主角当前处境", "发生打破日常的事件"][len(default_outline):]
        )
    chapter_plans = request.chapters or [
        ProjectChapterPlan(title="第 1 章 · 开篇", outline=default_outline)
    ]
    for index, chapter_plan in enumerate(chapter_plans, start=1):
        # ``volumeIndex`` is intentionally zero-based to match the array index
        # used by the wizard UI.  Omitted values retain legacy first-volume
        # behavior; invalid indexes fail before any chapter is persisted.
        volume_index = chapter_plan.volume_index
        if volume_index is None:
            target_volume = volumes[0]
        else:
            target_volume = volumes[volume_index]
        db.add(
            Chapter(
                id=f"ch_{secrets.token_hex(12)}",
                project_id=project.id,
                volume_id=target_volume.id,
                title=chapter_plan.title.strip(),
                idx=index,
                words=0,
                outline=[beat.strip() for beat in chapter_plan.outline if beat.strip()],
            )
        )

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


def _positioning_text(value: str | None) -> str:
    return (value or "").strip()


def _positioning_list(values: list[str] | None) -> list[str]:
    return [value.strip() for value in (values or []) if value.strip()]


def _positioning_snapshot(positioning: ProjectPositioning) -> ProjectPositioningRevision:
    return ProjectPositioningRevision(
        id=f"ppr_{secrets.token_hex(12)}",
        positioning_id=positioning.id,
        project_id=positioning.project_id,
        revision=positioning.revision,
        platform=positioning.platform,
        title_candidates=list(positioning.title_candidates or []),
        selling_point=positioning.selling_point,
        synopsis=positioning.synopsis,
        tags=list(positioning.tags or []),
        protagonist_dilemma=positioning.protagonist_dilemma,
        first_payoff=positioning.first_payoff,
        long_term_arc=positioning.long_term_arc,
        status=positioning.status,
    )


async def _locked_positioning(
    db: AsyncSession, project_id: str, expected_revision: int | None = None
) -> ProjectPositioning:
    positioning = await db.scalar(
        select(ProjectPositioning)
        .where(ProjectPositioning.project_id == project_id)
        .with_for_update()
    )
    if positioning is None:
        # Projects created before migration 029 have no card.  Create a blank
        # revision-zero card on their first write, preserving compatibility.
        if expected_revision not in (None, 0):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "PROJECT_POSITIONING_REVISION_CONFLICT",
                    "current_revision": 0,
                },
            )
        positioning = ProjectPositioning(
            id=f"pp_{secrets.token_hex(12)}",
            project_id=project_id,
            platform="general",
            title_candidates=[],
            selling_point="",
            synopsis="",
            tags=[],
            protagonist_dilemma="",
            first_payoff="",
            long_term_arc="",
            revision=0,
            status="draft",
        )
        db.add(positioning)
        await db.flush()
    if expected_revision is not None and positioning.revision != expected_revision:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROJECT_POSITIONING_REVISION_CONFLICT",
                "current_revision": positioning.revision,
            },
        )
    return positioning


@router.get("/{project_id}/positioning", response_model=ProjectPositioningOut)
async def get_project_positioning(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectPositioningOut:
    await verify_project_permission(project_id, ProjectPermission.VIEW, user, db)
    positioning = await _project_positioning(db, project_id)
    if positioning is None:
        # Do not mutate on a read; callers can create the first card via PUT.
        now = datetime.now(UTC)
        return ProjectPositioningOut(
            id="",
            project_id=project_id,
            platform="general",
            title_candidates=[],
            selling_point="",
            synopsis="",
            tags=[],
            protagonist_dilemma="",
            first_payoff="",
            long_term_arc="",
            revision=0,
            status="draft",
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )
    return _positioning_out(positioning)


@router.put("/{project_id}/positioning", response_model=ProjectPositioningOut)
async def update_project_positioning(
    project_id: str,
    request: ProjectPositioningPatch,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectPositioningOut:
    await verify_project_permission(project_id, ProjectPermission.MANAGE_PROJECT, user, db)
    positioning = await _locked_positioning(db, project_id, request.expected_revision)
    changes = request.model_dump(exclude_unset=True, exclude={"expected_revision"})
    if not changes:
        raise HTTPException(status_code=422, detail={"code": "POSITIONING_UPDATE_EMPTY"})
    if "title_candidates" in changes:
        positioning.title_candidates = _positioning_list(changes["title_candidates"])
    if "tags" in changes:
        positioning.tags = _positioning_list(changes["tags"])
    for field in (
        "selling_point",
        "synopsis",
        "protagonist_dilemma",
        "first_payoff",
        "long_term_arc",
    ):
        if field in changes:
            setattr(positioning, field, _positioning_text(changes[field]))
    for field in ("platform", "status"):
        if field in changes:
            setattr(positioning, field, changes[field])
    positioning.revision += 1
    await db.flush()
    db.add(_positioning_snapshot(positioning))
    await db.flush()
    await db.refresh(positioning)
    response = _positioning_out(positioning)
    await db.commit()
    return response


@router.get("/{project_id}/positioning/revisions", response_model=list[ProjectPositioningRevisionOut])
async def list_project_positioning_revisions(
    project_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ProjectPositioningRevisionOut]:
    await verify_project_permission(project_id, ProjectPermission.VIEW, user, db)
    rows = (
        await db.execute(
            select(ProjectPositioningRevision)
            .where(ProjectPositioningRevision.project_id == project_id)
            .order_by(ProjectPositioningRevision.revision.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [ProjectPositioningRevisionOut(**_positioning_out(row).model_dump()) for row in rows]


@router.get("/{project_id}/positioning/revisions/{revision}", response_model=ProjectPositioningRevisionOut)
async def get_project_positioning_revision(
    project_id: str,
    revision: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectPositioningRevisionOut:
    await verify_project_permission(project_id, ProjectPermission.VIEW, user, db)
    row = await db.scalar(
        select(ProjectPositioningRevision).where(
            ProjectPositioningRevision.project_id == project_id,
            ProjectPositioningRevision.revision == revision,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "PROJECT_POSITIONING_REVISION_NOT_FOUND"})
    return ProjectPositioningRevisionOut(**_positioning_out(row).model_dump())


@router.post(
    "/{project_id}/positioning/revisions/{revision}/restore",
    response_model=ProjectPositioningOut,
)
async def restore_project_positioning_revision(
    project_id: str,
    revision: int,
    request: ProjectPositioningRestoreRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectPositioningOut:
    await verify_project_permission(project_id, ProjectPermission.MANAGE_PROJECT, user, db)
    positioning = await _locked_positioning(db, project_id, request.expected_revision)
    snapshot = await db.scalar(
        select(ProjectPositioningRevision).where(
            ProjectPositioningRevision.project_id == project_id,
            ProjectPositioningRevision.revision == revision,
        )
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail={"code": "PROJECT_POSITIONING_REVISION_NOT_FOUND"})
    positioning.platform = snapshot.platform
    positioning.title_candidates = list(snapshot.title_candidates or [])
    positioning.selling_point = snapshot.selling_point
    positioning.synopsis = snapshot.synopsis
    positioning.tags = list(snapshot.tags or [])
    positioning.protagonist_dilemma = snapshot.protagonist_dilemma
    positioning.first_payoff = snapshot.first_payoff
    positioning.long_term_arc = snapshot.long_term_arc
    positioning.status = snapshot.status
    positioning.revision += 1
    await db.flush()
    db.add(_positioning_snapshot(positioning))
    await db.flush()
    await db.refresh(positioning)
    response = _positioning_out(positioning)
    await db.commit()
    return response


@router.get("/{project_id}/writing-progress", response_model=list[WritingProgressDayOut])
async def get_writing_progress(
    project_id: str,
    days: int = Query(default=30, ge=1, le=365),
    project: Project = Depends(verify_project_access),
    db: AsyncSession = Depends(get_db),
) -> list[WritingProgressDayOut]:
    """Return a continuous UTC daily-writing series for one accessible project."""
    today = datetime.now(UTC).date()
    start = today - timedelta(days=days - 1)
    rows = (
        await db.execute(
            select(
                ProjectDailyWriting.day,
                func.coalesce(func.sum(ProjectDailyWriting.words_added), 0),
                func.coalesce(func.sum(ProjectDailyWriting.saves), 0),
            )
            .where(
                ProjectDailyWriting.project_id == project.id,
                ProjectDailyWriting.day >= start,
                ProjectDailyWriting.day <= today,
            )
            .group_by(ProjectDailyWriting.day)
            .order_by(ProjectDailyWriting.day)
        )
    ).all()
    by_day = {
        row_day: {"words_added": int(words_added), "saves": int(saves)}
        for row_day, words_added, saves in rows
    }
    return [
        WritingProgressDayOut(
            date=(start + timedelta(days=offset)).isoformat(),
            words_added=by_day.get(
                start + timedelta(days=offset), {"words_added": 0, "saves": 0}
            )["words_added"],
            saves=by_day.get(
                start + timedelta(days=offset), {"words_added": 0, "saves": 0}
            )["saves"],
            target_words_daily=project.target_words_daily,
            target_met=by_day.get(
                start + timedelta(days=offset), {"words_added": 0, "saves": 0}
            )["words_added"] >= project.target_words_daily,
        )
        for offset in range(days)
    ]


@router.get("/{project_id}/notes", response_model=list[ProjectNoteOut])
async def list_project_notes(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List only the current user's private notes for one accessible project."""
    await verify_project_permission(project_id, ProjectPermission.VIEW, user, db)
    rows = (
        await db.execute(
            select(ProjectNote, Chapter)
            .outerjoin(
                Chapter,
                and_(
                    Chapter.id == ProjectNote.chapter_id,
                    Chapter.project_id == ProjectNote.project_id,
                ),
            )
            .where(ProjectNote.project_id == project_id, ProjectNote.user_id == user.id)
            .order_by(ProjectNote.created_at.desc(), ProjectNote.id.desc())
            .limit(200)
        )
    ).all()
    return [
        ProjectNoteOut(
            id=note.id,
            project_id=note.project_id,
            chapter_id=note.chapter_id,
            chapter_index=chapter.idx if chapter and chapter.deleted_at is None else None,
            chapter_title=chapter.title if chapter and chapter.deleted_at is None else None,
            content=note.content,
            created_at=note.created_at,
        )
        for note, chapter in rows
    ]


@router.post("/{project_id}/notes", response_model=ProjectNoteOut, status_code=201)
async def create_project_note(
    project_id: str,
    request: ProjectNoteCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Capture a private idea without granting body-edit permission on mobile."""
    await verify_project_permission(project_id, ProjectPermission.VIEW, user, db)
    content = request.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail={"code": "PROJECT_NOTE_EMPTY"})
    chapter = None
    if request.chapter_id:
        chapter = (
            await db.execute(
                select(Chapter).where(
                    Chapter.id == request.chapter_id,
                    Chapter.project_id == project_id,
                    Chapter.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if chapter is None:
            raise HTTPException(status_code=422, detail={"code": "PROJECT_NOTE_CHAPTER_INVALID"})
    note = ProjectNote(
        id=f"pn_{secrets.token_hex(12)}",
        project_id=project_id,
        user_id=user.id,
        chapter_id=chapter.id if chapter else None,
        content=content,
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return ProjectNoteOut(
        id=note.id,
        project_id=note.project_id,
        chapter_id=note.chapter_id,
        chapter_index=chapter.idx if chapter else None,
        chapter_title=chapter.title if chapter else None,
        content=note.content,
        created_at=note.created_at,
    )


@router.delete("/{project_id}/notes/{note_id}", status_code=204)
async def delete_project_note(
    project_id: str,
    note_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await verify_project_permission(project_id, ProjectPermission.VIEW, user, db)
    note = (
        await db.execute(
            select(ProjectNote).where(
                ProjectNote.id == note_id,
                ProjectNote.project_id == project_id,
                ProjectNote.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=404, detail={"code": "PROJECT_NOTE_NOT_FOUND"})
    await db.delete(note)
    await db.commit()


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: str,
    request: ProjectPatch,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    """Update shelf-level project metadata without replacing story settings."""
    project = await verify_project_permission(project_id, ProjectPermission.MANAGE_PROJECT, user, db)
    await _lock_project(db, project_id)
    changes = request.model_dump(exclude_unset=True)
    if "title" in changes:
        changes["title"] = _clean_required(changes["title"], "PROJECT_TITLE_REQUIRED")
    if "genre" in changes and changes["genre"] is not None:
        changes["genre"] = changes["genre"].strip() or None
    for field, value in changes.items():
        setattr(project, field, value)
    _touch_project(project)
    await db.commit()
    return await _project_out(db, project)


@router.post("/{project_id}/volumes", response_model=VolumeOut, status_code=201)
async def create_volume(
    project_id: str,
    request: VolumeCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VolumeOut:
    project = await verify_project_permission(project_id, ProjectPermission.MANAGE_OUTLINE, user, db)
    await _lock_project(db, project_id)
    volumes = await _active_volumes(db, project_id, lock=True)
    volume = Volume(
        id=f"v_{secrets.token_hex(12)}",
        project_id=project_id,
        title=_clean_required(request.title, "VOLUME_TITLE_REQUIRED"),
        summary=request.summary.strip() or None,
        idx=(len(volumes) + 1) * 1024,
    )
    db.add(volume)
    _touch_project(project)
    await db.commit()
    return _volume_out(volume)


@router.patch("/{project_id}/volumes/{volume_id}", response_model=VolumeOut)
async def update_volume(
    project_id: str,
    volume_id: str,
    request: VolumePatch,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VolumeOut:
    project = await verify_project_permission(project_id, ProjectPermission.MANAGE_OUTLINE, user, db)
    await _lock_project(db, project_id)
    volume = await _locked_active_volume(db, project_id, volume_id)
    changes = request.model_dump(exclude_unset=True)
    if "title" in changes:
        volume.title = _clean_required(changes["title"], "VOLUME_TITLE_REQUIRED")
    if "summary" in changes:
        volume.summary = changes["summary"].strip() or None
    _touch_project(project)
    await db.commit()
    return _volume_out(volume)


@router.put("/{project_id}/volumes/order", response_model=list[VolumeOut])
async def reorder_volumes(
    project_id: str,
    request: VolumeOrderRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[VolumeOut]:
    project = await verify_project_permission(project_id, ProjectPermission.MANAGE_OUTLINE, user, db)
    await _lock_project(db, project_id)
    volumes = await _active_volumes(db, project_id, lock=True)
    current = {volume.id: volume for volume in volumes}
    if len(request.volume_ids) != len(set(request.volume_ids)) or set(request.volume_ids) != set(current):
        raise HTTPException(status_code=422, detail={"code": "VOLUME_ORDER_MISMATCH"})
    ordered = [current[volume_id] for volume_id in request.volume_ids]
    _renumber_volumes(ordered)
    chapters = await _active_chapters(db, project_id, lock=True)
    _renumber_chapters(_chapters_in_volume_order(chapters, ordered))
    _touch_project(project)
    await db.commit()
    return [_volume_out(volume) for volume in ordered]


@router.delete("/{project_id}/volumes/{volume_id}", status_code=204)
async def trash_volume(
    project_id: str,
    volume_id: str,
    target_volume_id: str | None = Query(default=None, max_length=32),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    project = await verify_project_permission(project_id, ProjectPermission.MANAGE_OUTLINE, user, db)
    await _lock_project(db, project_id)
    volumes = await _active_volumes(db, project_id, lock=True)
    current = next((volume for volume in volumes if volume.id == volume_id), None)
    if current is None:
        raise HTTPException(status_code=404, detail={"code": "VOLUME_NOT_FOUND"})
    if len(volumes) == 1:
        raise HTTPException(status_code=409, detail={"code": "LAST_VOLUME"})
    chapters = await _active_chapters(db, project_id, lock=True)
    contained = [chapter for chapter in chapters if chapter.volume_id == volume_id]
    if contained:
        if target_volume_id is None:
            raise HTTPException(status_code=409, detail={"code": "TARGET_VOLUME_REQUIRED"})
        if target_volume_id == volume_id:
            raise HTTPException(status_code=422, detail={"code": "INVALID_TARGET_VOLUME"})
        await _locked_active_volume(db, project_id, target_volume_id)
        for chapter in contained:
            chapter.volume_id = target_volume_id
    current.deleted_at = datetime.now(UTC)
    remaining_volumes = [volume for volume in volumes if volume.id != volume_id]
    _renumber_volumes(remaining_volumes)
    _renumber_chapters(_chapters_in_volume_order(chapters, remaining_volumes))
    _touch_project(project)
    await db.commit()


@router.get("/{project_id}/trash", response_model=ProjectTrashOut)
async def get_project_trash(
    project_id: str,
    _project: Project = Depends(verify_project_access),
    db: AsyncSession = Depends(get_db),
) -> ProjectTrashOut:
    deleted_volumes = list(
        (
            await db.execute(
                select(Volume)
                .where(Volume.project_id == project_id, Volume.deleted_at.is_not(None))
                .order_by(Volume.deleted_at.desc())
            )
        ).scalars().all()
    )
    volume_titles = {
        volume.id: volume.title
        for volume in (
            await db.execute(select(Volume).where(Volume.project_id == project_id))
        ).scalars().all()
    }
    deleted_chapters = list(
        (
            await db.execute(
                select(Chapter)
                .where(Chapter.project_id == project_id, Chapter.deleted_at.is_not(None))
                .order_by(Chapter.deleted_at.desc())
            )
        ).scalars().all()
    )
    return ProjectTrashOut(
        volumes=[
            TrashVolumeOut(id=volume.id, title=volume.title, deleted_at=volume.deleted_at.isoformat())
            for volume in deleted_volumes
        ],
        chapters=[
            TrashChapterOut(
                id=chapter.id,
                title=chapter.title,
                words=chapter.words,
                volume_id=chapter.volume_id,
                volume_title=volume_titles.get(chapter.volume_id),
                deleted_at=chapter.deleted_at.isoformat(),
            )
            for chapter in deleted_chapters
        ],
    )


@router.post("/{project_id}/trash/volumes/{volume_id}/restore", response_model=VolumeOut)
async def restore_volume(
    project_id: str,
    volume_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VolumeOut:
    project = await verify_project_permission(project_id, ProjectPermission.MANAGE_OUTLINE, user, db)
    await _lock_project(db, project_id)
    result = await db.execute(
        select(Volume)
        .where(Volume.id == volume_id, Volume.project_id == project_id, Volume.deleted_at.is_not(None))
        .with_for_update()
    )
    volume = result.scalar_one_or_none()
    if volume is None:
        raise HTTPException(status_code=404, detail={"code": "TRASH_VOLUME_NOT_FOUND"})
    active = await _active_volumes(db, project_id, lock=True)
    volume.deleted_at = None
    active.append(volume)
    _renumber_volumes(active)
    _touch_project(project)
    await db.commit()
    return _volume_out(volume)


@router.delete("/{project_id}/trash/volumes/{volume_id}", status_code=204)
async def delete_volume_permanently(
    project_id: str,
    volume_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    project = await verify_project_permission(project_id, ProjectPermission.MANAGE_OUTLINE, user, db)
    await _lock_project(db, project_id)
    result = await db.execute(
        select(Volume)
        .where(Volume.id == volume_id, Volume.project_id == project_id, Volume.deleted_at.is_not(None))
        .with_for_update()
    )
    volume = result.scalar_one_or_none()
    if volume is None:
        raise HTTPException(status_code=404, detail={"code": "TRASH_VOLUME_NOT_FOUND"})
    chapter_count = await db.scalar(select(func.count(Chapter.id)).where(Chapter.volume_id == volume_id))
    if chapter_count:
        raise HTTPException(status_code=409, detail={"code": "TRASH_VOLUME_NOT_EMPTY"})
    await db.delete(volume)
    _touch_project(project)
    await db.commit()


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
        .where(Chapter.project_id == project_id, Chapter.deleted_at.is_(None))
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
            pov_entry_id=ch.pov_entry_id,
            pov_revision=ch.pov_revision,
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
    project = await verify_project_permission(project_id, ProjectPermission.MANAGE_OUTLINE, user, db)
    await _lock_project(db, project_id)
    volume = await _locked_active_volume(db, project_id, request.volume_id)
    chapters = await _active_chapters(db, project_id, lock=True)
    if request.after_index > 0:
        anchor = next((chapter for chapter in chapters if chapter.idx == request.after_index), None)
        if anchor is None:
            raise HTTPException(status_code=422, detail={"code": "AFTER_CHAPTER_NOT_FOUND"})
        if anchor.volume_id != volume.id:
            raise HTTPException(status_code=422, detail={"code": "AFTER_CHAPTER_NOT_IN_VOLUME"})
        insertion_index = chapters.index(anchor) + 1
    else:
        volume_chapter_indexes = [
            index for index, existing in enumerate(chapters) if existing.volume_id == volume.id
        ]
        if volume_chapter_indexes:
            insertion_index = volume_chapter_indexes[0]
        else:
            volumes = await _active_volumes(db, project_id, lock=True)
            order = {item.id: index for index, item in enumerate(volumes)}
            insertion_index = next(
                (
                    index
                    for index, existing in enumerate(chapters)
                    if order.get(existing.volume_id, len(volumes)) > order[volume.id]
                ),
                len(chapters),
            )
    chapter = Chapter(
        id=f"ch_{secrets.token_hex(12)}",
        project_id=project_id,
        volume_id=volume.id,
        title="新章节 · 待规划",
        idx=0,
        words=0,
        outline=[],
    )
    db.add(chapter)
    chapters.insert(insertion_index, chapter)
    _renumber_chapters(chapters)
    chapter.title = f"第 {chapter.idx} 章 · 待规划"
    _touch_project(project)
    await db.commit()
    await db.refresh(chapter)
    return _chapter_list_item(chapter)


@router.put("/{project_id}/chapters/{chapter_id}/position", response_model=ChapterListItem)
async def move_chapter(
    project_id: str,
    chapter_id: str,
    request: ChapterPositionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChapterListItem:
    """Move a chapter within the book or into another active volume."""
    project = await verify_project_permission(project_id, ProjectPermission.MANAGE_OUTLINE, user, db)
    await _lock_project(db, project_id)
    volumes = await _active_volumes(db, project_id, lock=True)
    volume_ids = {volume.id for volume in volumes}
    if request.volume_id not in volume_ids:
        raise HTTPException(status_code=422, detail={"code": "VOLUME_NOT_IN_PROJECT"})
    chapters = await _active_chapters(db, project_id, lock=True)
    chapter = next((item for item in chapters if item.id == chapter_id), None)
    if chapter is None:
        raise HTTPException(status_code=404, detail={"code": "CHAPTER_NOT_FOUND"})
    chapters.remove(chapter)

    if request.placement == "after":
        if not request.after_chapter_id or request.after_chapter_id == chapter_id:
            raise HTTPException(status_code=422, detail={"code": "AFTER_CHAPTER_REQUIRED"})
        anchor = next((item for item in chapters if item.id == request.after_chapter_id), None)
        if anchor is None or anchor.volume_id != request.volume_id:
            raise HTTPException(status_code=422, detail={"code": "AFTER_CHAPTER_NOT_IN_VOLUME"})
        insertion_index = chapters.index(anchor) + 1
    else:
        target_indexes = [index for index, item in enumerate(chapters) if item.volume_id == request.volume_id]
        if target_indexes:
            insertion_index = target_indexes[0] if request.placement == "first" else target_indexes[-1] + 1
        else:
            volume_order = {volume.id: index for index, volume in enumerate(volumes)}
            target_order = volume_order[request.volume_id]
            insertion_index = next(
                (
                    index
                    for index, item in enumerate(chapters)
                    if volume_order.get(item.volume_id, len(volumes)) > target_order
                ),
                len(chapters),
            )

    chapter.volume_id = request.volume_id
    chapters.insert(insertion_index, chapter)
    _renumber_chapters(chapters)
    _touch_project(project)
    await db.commit()
    await db.refresh(chapter)
    return _chapter_list_item(chapter)


@router.delete("/{project_id}/chapters/{chapter_id}", status_code=204)
async def trash_chapter(
    project_id: str,
    chapter_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    project = await verify_project_permission(project_id, ProjectPermission.MANAGE_OUTLINE, user, db)
    await _lock_project(db, project_id)
    chapters = await _active_chapters(db, project_id, lock=True)
    chapter = next((item for item in chapters if item.id == chapter_id), None)
    if chapter is None:
        raise HTTPException(status_code=404, detail={"code": "CHAPTER_NOT_FOUND"})
    if len(chapters) == 1:
        raise HTTPException(status_code=409, detail={"code": "LAST_CHAPTER"})
    chapter.deleted_at = datetime.now(UTC)
    _renumber_chapters([item for item in chapters if item.id != chapter_id])
    _touch_project(project)
    await db.commit()


@router.post("/{project_id}/trash/chapters/{chapter_id}/restore", response_model=ChapterListItem)
async def restore_chapter(
    project_id: str,
    chapter_id: str,
    request: RestoreChapterRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChapterListItem:
    project = await verify_project_permission(project_id, ProjectPermission.MANAGE_OUTLINE, user, db)
    await _lock_project(db, project_id)
    result = await db.execute(
        select(Chapter)
        .where(Chapter.id == chapter_id, Chapter.project_id == project_id, Chapter.deleted_at.is_not(None))
        .with_for_update()
    )
    chapter = result.scalar_one_or_none()
    if chapter is None:
        raise HTTPException(status_code=404, detail={"code": "TRASH_CHAPTER_NOT_FOUND"})
    volumes = await _active_volumes(db, project_id, lock=True)
    active_volume_ids = {volume.id for volume in volumes}
    target_volume_id = request.volume_id or chapter.volume_id
    if target_volume_id not in active_volume_ids:
        if request.volume_id is not None:
            raise HTTPException(status_code=422, detail={"code": "VOLUME_NOT_IN_PROJECT"})
        target_volume_id = volumes[0].id
    chapters = await _active_chapters(db, project_id, lock=True)
    chapter.deleted_at = None
    chapter.volume_id = target_volume_id
    chapters.append(chapter)
    _renumber_chapters(_chapters_in_volume_order(chapters, volumes))
    _touch_project(project)
    await db.commit()
    await db.refresh(chapter)
    return _chapter_list_item(chapter)


@router.delete("/{project_id}/trash/chapters/{chapter_id}", status_code=204)
async def delete_chapter_permanently(
    project_id: str,
    chapter_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    project = await verify_project_permission(project_id, ProjectPermission.MANAGE_OUTLINE, user, db)
    await _lock_project(db, project_id)
    result = await db.execute(
        select(Chapter)
        .where(Chapter.id == chapter_id, Chapter.project_id == project_id, Chapter.deleted_at.is_not(None))
        .with_for_update()
    )
    chapter = result.scalar_one_or_none()
    if chapter is None:
        raise HTTPException(status_code=404, detail={"code": "TRASH_CHAPTER_NOT_FOUND"})
    await db.delete(chapter)
    _touch_project(project)
    await db.commit()
