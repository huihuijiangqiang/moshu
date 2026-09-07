"""漫剧改编与静态分镜 API。

当前只管理结构化稿件和人物视觉约束，不启动图片/视频生成任务。
"""
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import Adaptation, Episode, Scene, Shot, VisualProfile
from db.session import get_db

router = APIRouter()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:24]}"


class AdaptationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    format: str = "comic_drama"
    aspect_ratio: str = "9:16"
    style_profile: dict = Field(default_factory=dict)


class AdaptationOut(AdaptationCreate):
    id: str
    project_id: str
    status: str
    model_config = ConfigDict(from_attributes=True)


class EpisodeCreate(BaseModel):
    number: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    source_chapter_ids: list[str] = Field(default_factory=list)
    target_duration: int = Field(default=90, ge=1, le=1800)


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
    character_entry_ids: list[str] = Field(default_factory=list)
    summary: str = ""


class SceneOut(SceneCreate):
    id: str
    episode_id: str
    model_config = ConfigDict(from_attributes=True)


class ShotCreate(BaseModel):
    order: int = Field(default=1, ge=1)
    shot_type: str = "medium"
    camera: str = "static"
    duration_target: int = Field(default=4, ge=1, le=120)
    action: str = ""
    dialogue: str = ""
    narration: str = ""
    visual_prompt: str = ""
    reference_asset_ids: list[str] = Field(default_factory=list)


class ShotPatch(BaseModel):
    order: int | None = Field(default=None, ge=1)
    shot_type: str | None = None
    camera: str | None = None
    duration_target: int | None = Field(default=None, ge=1, le=120)
    action: str | None = None
    dialogue: str | None = None
    narration: str | None = None
    visual_prompt: str | None = None
    reference_asset_ids: list[str] | None = None
    status: str | None = None


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


async def require_adaptation(adaptation_id: str, db: AsyncSession) -> Adaptation:
    item = await db.get(Adaptation, adaptation_id)
    if not item:
        raise HTTPException(status_code=404, detail="漫剧改编版本不存在")
    return item


async def require_episode(episode_id: str, db: AsyncSession) -> Episode:
    item = await db.get(Episode, episode_id)
    if not item:
        raise HTTPException(status_code=404, detail="漫剧集不存在")
    return item


async def require_scene(scene_id: str, db: AsyncSession) -> Scene:
    item = await db.get(Scene, scene_id)
    if not item:
        raise HTTPException(status_code=404, detail="分镜场景不存在")
    return item


@router.get("/projects/{project_id}/adaptations", response_model=list[AdaptationOut])
async def list_adaptations(project_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Adaptation).where(Adaptation.project_id == project_id).order_by(Adaptation.created_at))
    return result.scalars().all()


@router.post("/projects/{project_id}/adaptations", response_model=AdaptationOut, status_code=201)
async def create_adaptation(project_id: str, payload: AdaptationCreate, db: AsyncSession = Depends(get_db)):
    item = Adaptation(id=new_id("adp"), project_id=project_id, **payload.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.get("/adaptations/{adaptation_id}/episodes", response_model=list[EpisodeOut])
async def list_episodes(adaptation_id: str, db: AsyncSession = Depends(get_db)):
    await require_adaptation(adaptation_id, db)
    result = await db.execute(select(Episode).where(Episode.adaptation_id == adaptation_id).order_by(Episode.number))
    return result.scalars().all()


@router.post("/adaptations/{adaptation_id}/episodes", response_model=EpisodeOut, status_code=201)
async def create_episode(adaptation_id: str, payload: EpisodeCreate, db: AsyncSession = Depends(get_db)):
    await require_adaptation(adaptation_id, db)
    item = Episode(id=new_id("ep"), adaptation_id=adaptation_id, **payload.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.get("/episodes/{episode_id}/scenes", response_model=list[SceneOut])
async def list_scenes(episode_id: str, db: AsyncSession = Depends(get_db)):
    await require_episode(episode_id, db)
    result = await db.execute(select(Scene).where(Scene.episode_id == episode_id).order_by(Scene.order))
    return result.scalars().all()


@router.post("/episodes/{episode_id}/scenes", response_model=SceneOut, status_code=201)
async def create_scene(episode_id: str, payload: SceneCreate, db: AsyncSession = Depends(get_db)):
    await require_episode(episode_id, db)
    item = Scene(id=new_id("scn"), episode_id=episode_id, **payload.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.get("/scenes/{scene_id}/shots", response_model=list[ShotOut])
async def list_shots(scene_id: str, db: AsyncSession = Depends(get_db)):
    await require_scene(scene_id, db)
    result = await db.execute(select(Shot).where(Shot.scene_id == scene_id).order_by(Shot.order))
    return result.scalars().all()


@router.post("/scenes/{scene_id}/shots", response_model=ShotOut, status_code=201)
async def create_shot(scene_id: str, payload: ShotCreate, db: AsyncSession = Depends(get_db)):
    await require_scene(scene_id, db)
    item = Shot(id=new_id("shot"), scene_id=scene_id, **payload.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.patch("/shots/{shot_id}", response_model=ShotOut)
async def update_shot(shot_id: str, payload: ShotPatch, db: AsyncSession = Depends(get_db)):
    item = await db.get(Shot, shot_id)
    if not item:
        raise HTTPException(status_code=404, detail="分镜不存在")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return item


@router.get("/projects/{project_id}/adaptations/{adaptation_id}/visual-profiles", response_model=list[VisualProfileOut])
async def list_visual_profiles(project_id: str, adaptation_id: str, db: AsyncSession = Depends(get_db)):
    adaptation = await require_adaptation(adaptation_id, db)
    if adaptation.project_id != project_id:
        raise HTTPException(status_code=404, detail="漫剧改编版本不存在")
    result = await db.execute(
        select(VisualProfile).where(VisualProfile.adaptation_id == adaptation_id).order_by(VisualProfile.display_name)
    )
    return result.scalars().all()


@router.post("/adaptations/{adaptation_id}/visual-profiles", response_model=VisualProfileOut, status_code=201)
async def create_visual_profile(adaptation_id: str, payload: VisualProfileCreate, db: AsyncSession = Depends(get_db)):
    await require_adaptation(adaptation_id, db)
    item = VisualProfile(id=new_id("vp"), adaptation_id=adaptation_id, **payload.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.patch("/visual-profiles/{profile_id}", response_model=VisualProfileOut)
async def update_visual_profile(profile_id: str, payload: VisualProfilePatch, db: AsyncSession = Depends(get_db)):
    item = await db.get(VisualProfile, profile_id)
    if not item:
        raise HTTPException(status_code=404, detail="人物视觉档案不存在")
    data = payload.model_dump(exclude_unset=True)
    if any(key != "locked" for key in data):
        item.version += 1
    for key, value in data.items():
        setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return item
