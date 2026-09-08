"""漫剧改编与静态分镜 API。

当前只管理结构化稿件和人物视觉约束，不启动图片/视频生成任务。
"""
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db import Adaptation, Chapter, CodexEntry, Episode, Scene, Shot, VisualProfile
from db.session import get_db
from api.auth import ProjectPermission, get_current_user, verify_project_permission
from db.models_core import User

router = APIRouter()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:24]}"


class AdaptationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    format: Literal["comic_drama"] = "comic_drama"
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    style_profile: dict = Field(default_factory=dict)


class AdaptationOut(AdaptationCreate):
    id: str
    project_id: str
    status: str
    model_config = ConfigDict(from_attributes=True)


class EpisodeCreate(BaseModel):
    number: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    source_chapter_ids: list[str] = Field(min_length=1, max_length=100)
    target_duration: int = Field(default=90, ge=1, le=1800)


class EpisodePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    source_chapter_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    target_duration: int | None = Field(default=None, ge=1, le=1800)
    status: Literal["draft", "in_review", "approved"] | None = None


class EpisodeOut(EpisodeCreate):
    id: str
    adaptation_id: str
    status: str
    model_config = ConfigDict(from_attributes=True)


class SceneCreate(BaseModel):
    order: int = Field(default=1, ge=1)
    purpose: str = ""
    location_entry_id: str | None = None
    time_anchor: str = ""
    character_entry_ids: list[str] = Field(default_factory=list, max_length=50)
    summary: str = ""


class ScenePatch(BaseModel):
    purpose: str | None = Field(default=None, max_length=200)
    location_entry_id: str | None = None
    time_anchor: str | None = Field(default=None, max_length=100)
    character_entry_ids: list[str] | None = Field(default=None, max_length=50)
    summary: str | None = None


class SceneOut(SceneCreate):
    id: str
    episode_id: str
    model_config = ConfigDict(from_attributes=True)


class ShotCreate(BaseModel):
    order: int = Field(default=1, ge=1)
    shot_type: Literal["wide", "medium", "close", "detail", "overhead"] = "medium"
    camera: str = "static"
    duration_target: int = Field(default=4, ge=1, le=120)
    action: str = ""
    dialogue: str = ""
    narration: str = ""
    visual_prompt: str = ""
    reference_asset_ids: list[str] = Field(default_factory=list)


class ShotPatch(BaseModel):
    order: int | None = Field(default=None, ge=1)
    shot_type: Literal["wide", "medium", "close", "detail", "overhead"] | None = None
    camera: str | None = None
    duration_target: int | None = Field(default=None, ge=1, le=120)
    action: str | None = None
    dialogue: str | None = None
    narration: str | None = None
    visual_prompt: str | None = None
    reference_asset_ids: list[str] | None = None
    status: Literal["draft", "approved"] | None = None


class ShotOut(ShotCreate):
    id: str
    scene_id: str
    status: str
    model_config = ConfigDict(from_attributes=True)


class VisualProfileCreate(BaseModel):
    codex_entry_id: str
    display_name: str = Field(min_length=1, max_length=200)
    style: str = ""
    appearance: str = ""
    costume: str = ""
    palette: list[str] = Field(default_factory=list)
    reference_asset_ids: list[str] = Field(default_factory=list)
    notes: str = ""


class VisualProfilePatch(BaseModel):
    display_name: str | None = None
    style: str | None = None
    appearance: str | None = None
    costume: str | None = None
    palette: list[str] | None = None
    reference_asset_ids: list[str] | None = None
    notes: str | None = None
    locked: bool | None = None


class VisualProfileOut(VisualProfileCreate):
    id: str
    adaptation_id: str
    version: int
    locked: bool
    model_config = ConfigDict(from_attributes=True)


async def require_adaptation(
    adaptation_id: str,
    db: AsyncSession,
    user: User,
    permission: ProjectPermission = ProjectPermission.VIEW,
) -> Adaptation:
    item = await db.get(Adaptation, adaptation_id)
    if not item:
        raise HTTPException(status_code=404, detail="漫剧改编版本不存在")
    await verify_project_permission(item.project_id, permission, user, db)
    return item


async def require_episode(
    episode_id: str,
    db: AsyncSession,
    user: User,
    permission: ProjectPermission = ProjectPermission.VIEW,
) -> Episode:
    item = await db.get(Episode, episode_id)
    if not item:
        raise HTTPException(status_code=404, detail="漫剧集不存在")
    await require_adaptation(item.adaptation_id, db, user, permission)
    return item


async def require_scene(
    scene_id: str,
    db: AsyncSession,
    user: User,
    permission: ProjectPermission = ProjectPermission.VIEW,
) -> Scene:
    item = await db.get(Scene, scene_id)
    if not item:
        raise HTTPException(status_code=404, detail="分镜场景不存在")
    await require_episode(item.episode_id, db, user, permission)
    return item


def unique_ids(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


async def validate_chapter_ids(project_id: str, chapter_ids: list[str], db: AsyncSession) -> list[str]:
    ids = unique_ids(chapter_ids)
    if not ids:
        return []
    result = await db.execute(
        select(Chapter.id).where(
            Chapter.project_id == project_id,
            Chapter.id.in_(ids),
            Chapter.deleted_at.is_(None),
        )
    )
    found = set(result.scalars().all())
    missing = [item_id for item_id in ids if item_id not in found]
    if missing:
        raise HTTPException(status_code=422, detail={"code": "invalid_source_chapters", "ids": missing})
    return ids


async def validate_scene_references(
    project_id: str,
    location_entry_id: str | None,
    character_entry_ids: list[str],
    db: AsyncSession,
) -> tuple[str | None, list[str]]:
    character_ids = unique_ids(character_entry_ids)
    requested = unique_ids([*(character_ids), *([location_entry_id] if location_entry_id else [])])
    if not requested:
        return location_entry_id, character_ids
    result = await db.execute(
        select(CodexEntry.id, CodexEntry.kind).where(
            CodexEntry.project_id == project_id,
            CodexEntry.id.in_(requested),
            CodexEntry.status == "confirmed",
        )
    )
    entries = dict(result.all())
    invalid = [item_id for item_id in requested if item_id not in entries]
    if invalid:
        raise HTTPException(status_code=422, detail={"code": "invalid_scene_references", "ids": invalid})
    if location_entry_id and entries[location_entry_id] != "location":
        raise HTTPException(status_code=422, detail={"code": "scene_location_must_be_location"})
    wrong_characters = [item_id for item_id in character_ids if entries[item_id] != "character"]
    if wrong_characters:
        raise HTTPException(
            status_code=422,
            detail={"code": "scene_characters_must_be_characters", "ids": wrong_characters},
        )
    return location_entry_id, character_ids


async def validate_visual_profile_entry(project_id: str, codex_entry_id: str, db: AsyncSession) -> None:
    kind = await db.scalar(
        select(CodexEntry.kind).where(
            CodexEntry.id == codex_entry_id,
            CodexEntry.project_id == project_id,
            CodexEntry.status == "confirmed",
        )
    )
    if kind != "character":
        raise HTTPException(status_code=422, detail={"code": "visual_profile_requires_character"})


@router.get("/projects/{project_id}/adaptations", response_model=list[AdaptationOut])
async def list_adaptations(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await verify_project_permission(project_id, ProjectPermission.VIEW, user, db)
    result = await db.execute(select(Adaptation).where(Adaptation.project_id == project_id).order_by(Adaptation.created_at))
    return result.scalars().all()


@router.post("/projects/{project_id}/adaptations", response_model=AdaptationOut, status_code=201)
async def create_adaptation(
    project_id: str,
    payload: AdaptationCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await verify_project_permission(project_id, ProjectPermission.MANAGE_OUTLINE, user, db)
    item = Adaptation(id=new_id("adp"), project_id=project_id, **payload.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.get("/adaptations/{adaptation_id}/episodes", response_model=list[EpisodeOut])
async def list_episodes(adaptation_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await require_adaptation(adaptation_id, db, user)
    result = await db.execute(select(Episode).where(Episode.adaptation_id == adaptation_id).order_by(Episode.number))
    return result.scalars().all()


@router.post("/adaptations/{adaptation_id}/episodes", response_model=EpisodeOut, status_code=201)
async def create_episode(
    adaptation_id: str,
    payload: EpisodeCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    adaptation = await require_adaptation(adaptation_id, db, user, ProjectPermission.MANAGE_OUTLINE)
    data = payload.model_dump()
    data["source_chapter_ids"] = await validate_chapter_ids(
        adaptation.project_id, payload.source_chapter_ids, db
    )
    item = Episode(id=new_id("ep"), adaptation_id=adaptation_id, **data)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.patch("/episodes/{episode_id}", response_model=EpisodeOut)
async def update_episode(
    episode_id: str,
    payload: EpisodePatch,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = await require_episode(episode_id, db, user, ProjectPermission.MANAGE_OUTLINE)
    adaptation = await db.get(Adaptation, item.adaptation_id)
    data = payload.model_dump(exclude_unset=True)
    if "source_chapter_ids" in data:
        data["source_chapter_ids"] = await validate_chapter_ids(
            adaptation.project_id, data["source_chapter_ids"], db
        )
    for key, value in data.items():
        setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return item


@router.get("/episodes/{episode_id}/scenes", response_model=list[SceneOut])
async def list_scenes(episode_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await require_episode(episode_id, db, user)
    result = await db.execute(select(Scene).where(Scene.episode_id == episode_id).order_by(Scene.order))
    return result.scalars().all()


@router.post("/episodes/{episode_id}/scenes", response_model=SceneOut, status_code=201)
async def create_scene(
    episode_id: str,
    payload: SceneCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    episode = await require_episode(episode_id, db, user, ProjectPermission.MANAGE_OUTLINE)
    adaptation = await db.get(Adaptation, episode.adaptation_id)
    data = payload.model_dump()
    data["location_entry_id"], data["character_entry_ids"] = await validate_scene_references(
        adaptation.project_id,
        payload.location_entry_id,
        payload.character_entry_ids,
        db,
    )
    item = Scene(id=new_id("scn"), episode_id=episode_id, **data)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.patch("/scenes/{scene_id}", response_model=SceneOut)
async def update_scene(
    scene_id: str,
    payload: ScenePatch,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = await require_scene(scene_id, db, user, ProjectPermission.MANAGE_OUTLINE)
    episode = await db.get(Episode, item.episode_id)
    adaptation = await db.get(Adaptation, episode.adaptation_id)
    data = payload.model_dump(exclude_unset=True)
    if "location_entry_id" in data or "character_entry_ids" in data:
        data["location_entry_id"], data["character_entry_ids"] = await validate_scene_references(
            adaptation.project_id,
            data.get("location_entry_id", item.location_entry_id),
            data.get("character_entry_ids", item.character_entry_ids),
            db,
        )
    for key, value in data.items():
        setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return item


@router.get("/scenes/{scene_id}/shots", response_model=list[ShotOut])
async def list_shots(scene_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await require_scene(scene_id, db, user)
    result = await db.execute(select(Shot).where(Shot.scene_id == scene_id).order_by(Shot.order))
    return result.scalars().all()


@router.post("/scenes/{scene_id}/shots", response_model=ShotOut, status_code=201)
async def create_shot(
    scene_id: str,
    payload: ShotCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await require_scene(scene_id, db, user, ProjectPermission.MANAGE_OUTLINE)
    item = Shot(id=new_id("shot"), scene_id=scene_id, **payload.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.patch("/shots/{shot_id}", response_model=ShotOut)
async def update_shot(
    shot_id: str,
    payload: ShotPatch,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = await db.get(Shot, shot_id)
    if not item:
        raise HTTPException(status_code=404, detail="分镜不存在")
    await require_scene(item.scene_id, db, user, ProjectPermission.MANAGE_OUTLINE)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return item


@router.get("/projects/{project_id}/adaptations/{adaptation_id}/visual-profiles", response_model=list[VisualProfileOut])
async def list_visual_profiles(
    project_id: str,
    adaptation_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    adaptation = await require_adaptation(adaptation_id, db, user)
    if adaptation.project_id != project_id:
        raise HTTPException(status_code=404, detail="漫剧改编版本不存在")
    result = await db.execute(
        select(VisualProfile).where(VisualProfile.adaptation_id == adaptation_id).order_by(VisualProfile.display_name)
    )
    return result.scalars().all()


@router.post("/adaptations/{adaptation_id}/visual-profiles", response_model=VisualProfileOut, status_code=201)
async def create_visual_profile(
    adaptation_id: str,
    payload: VisualProfileCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    adaptation = await require_adaptation(adaptation_id, db, user, ProjectPermission.MANAGE_CODEX)
    await validate_visual_profile_entry(adaptation.project_id, payload.codex_entry_id, db)
    existing = await db.scalar(
        select(VisualProfile.id).where(
            VisualProfile.adaptation_id == adaptation_id,
            VisualProfile.codex_entry_id == payload.codex_entry_id,
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail={"code": "visual_profile_already_exists"})
    item = VisualProfile(id=new_id("vp"), adaptation_id=adaptation_id, **payload.model_dump())
    db.add(item)
    try:
        await db.commit()
    except IntegrityError as caught:
        await db.rollback()
        raise HTTPException(status_code=409, detail={"code": "visual_profile_already_exists"}) from caught
    await db.refresh(item)
    return item


@router.patch("/visual-profiles/{profile_id}", response_model=VisualProfileOut)
async def update_visual_profile(
    profile_id: str,
    payload: VisualProfilePatch,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = await db.get(VisualProfile, profile_id)
    if not item:
        raise HTTPException(status_code=404, detail="人物视觉档案不存在")
    await require_adaptation(item.adaptation_id, db, user, ProjectPermission.MANAGE_CODEX)
    data = payload.model_dump(exclude_unset=True)
    if any(key != "locked" for key in data):
        item.version += 1
    for key, value in data.items():
        setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return item
