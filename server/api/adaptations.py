"""Comic-drama storyboard, review and private visual-asset API."""
import hashlib
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from api.auth import ProjectPermission, get_current_user, verify_project_permission
from config import settings
from db import Adaptation, Chapter, CodexEntry, Episode, ProductionAsset, Scene, Shot, VisualProfile
from db.models_core import User
from db.session import get_db
from services.production_assets import IMAGE_EXTENSIONS, InvalidProductionImageError, asset_path, normalize_image

router = APIRouter()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:24]}"


class ProductionAssetOut(BaseModel):
    id: str
    adaptation_id: str
    episode_id: str | None
    shot_id: str | None
    visual_profile_id: str | None
    kind: str
    original_filename: str
    mime_type: str
    byte_size: int
    width: int | None
    height: int | None
    sha256: str
    status: str
    created_by: str
    rejection_reason: str | None = None
    content_url: str


class ProductionAssetPatch(BaseModel):
    status: Literal["draft", "approved", "rejected"]
    rejection_reason: str | None = Field(default=None, max_length=500)


def _asset_out(asset: ProductionAsset) -> ProductionAssetOut:
    return ProductionAssetOut(
        id=asset.id, adaptation_id=asset.adaptation_id, episode_id=asset.episode_id,
        shot_id=asset.shot_id, visual_profile_id=asset.visual_profile_id, kind=asset.kind, original_filename=asset.original_filename,
        mime_type=asset.mime_type, byte_size=asset.byte_size, width=asset.width,
        height=asset.height, sha256=asset.sha256, status=asset.status,
        created_by=asset.created_by, rejection_reason=asset.rejection_reason,
        content_url=f"/assets/{asset.id}/content",
    )


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


class ReorderRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)


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


async def require_asset(asset_id: str, db: AsyncSession, user: User, permission: ProjectPermission = ProjectPermission.VIEW) -> ProductionAsset:
    asset = await db.get(ProductionAsset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="漫剧素材不存在")
    await require_adaptation(asset.adaptation_id, db, user, permission)
    return asset


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


@router.get("/adaptations/{adaptation_id}/assets", response_model=list[ProductionAssetOut])
async def list_production_assets(
    adaptation_id: str,
    episode_id: str | None = None,
    shot_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await require_adaptation(adaptation_id, db, user)
    query = select(ProductionAsset).where(ProductionAsset.adaptation_id == adaptation_id)
    if episode_id:
        query = query.where(ProductionAsset.episode_id == episode_id)
    if shot_id:
        query = query.where(ProductionAsset.shot_id == shot_id)
    result = await db.execute(query.order_by(ProductionAsset.created_at, ProductionAsset.id))
    return [_asset_out(asset) for asset in result.scalars().all()]


@router.post("/adaptations/{adaptation_id}/assets", response_model=ProductionAssetOut, status_code=201)
async def upload_production_asset(
    adaptation_id: str,
    file: UploadFile = File(...),
    episode_id: str | None = Form(default=None),
    shot_id: str | None = Form(default=None),
    visual_profile_id: str | None = Form(default=None),
    kind: Literal["image", "reference", "character_sheet"] = Form(default="image"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    adaptation = await require_adaptation(adaptation_id, db, user, ProjectPermission.MANAGE_OUTLINE)
    visual_profile: VisualProfile | None = None
    if file.content_type not in IMAGE_EXTENSIONS:
        raise HTTPException(status_code=415, detail={"code": "unsupported_image_type", "allowed": sorted(IMAGE_EXTENSIONS)})
    if visual_profile_id:
        visual_profile = await db.get(VisualProfile, visual_profile_id)
        if visual_profile is None or visual_profile.adaptation_id != adaptation.id:
            raise HTTPException(status_code=422, detail={"code": "asset_visual_profile_outside_adaptation"})
        if kind != "character_sheet":
            raise HTTPException(status_code=422, detail={"code": "visual_profile_asset_must_be_character_sheet"})
    if shot_id and visual_profile_id:
        raise HTTPException(status_code=422, detail={"code": "asset_target_is_ambiguous"})
    if shot_id:
        shot = await db.get(Shot, shot_id)
        if not shot:
            raise HTTPException(status_code=404, detail="分镜不存在")
        scene = await require_scene(shot.scene_id, db, user, ProjectPermission.MANAGE_OUTLINE)
        episode = await db.get(Episode, scene.episode_id)
        if episode is None or episode.adaptation_id != adaptation.id:
            raise HTTPException(status_code=422, detail={"code": "asset_shot_outside_adaptation"})
        if episode_id and episode_id != episode.id:
            raise HTTPException(status_code=422, detail={"code": "asset_episode_mismatch"})
        episode_id = episode.id
    elif episode_id:
        episode = await require_episode(episode_id, db, user, ProjectPermission.MANAGE_OUTLINE)
        if episode.adaptation_id != adaptation.id:
            raise HTTPException(status_code=422, detail={"code": "asset_episode_outside_adaptation"})

    data = await file.read(settings.production_asset_max_bytes + 1)
    if len(data) > settings.production_asset_max_bytes:
        raise HTTPException(status_code=413, detail={"code": "asset_too_large", "max_bytes": settings.production_asset_max_bytes})
    try:
        data, width, height = await run_in_threadpool(normalize_image, data, file.content_type)
    except InvalidProductionImageError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_image", "message": "图片损坏、尺寸过大或包含不支持的动画"},
        ) from error
    if len(data) > settings.production_asset_max_bytes:
        raise HTTPException(status_code=413, detail={"code": "asset_too_large", "max_bytes": settings.production_asset_max_bytes})

    asset_id = new_id("asset")
    extension = IMAGE_EXTENSIONS[file.content_type]
    storage_key = f"{adaptation.id}/{asset_id}.{extension}"
    target = asset_path(storage_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    await run_in_threadpool(target.write_bytes, data)
    asset = ProductionAsset(
        id=asset_id,
        adaptation_id=adaptation.id,
        episode_id=episode_id,
        shot_id=shot_id,
        visual_profile_id=visual_profile_id,
        kind=kind,
        original_filename=Path(file.filename or f"{asset_id}.{extension}").name[:255],
        storage_key=storage_key,
        mime_type=file.content_type,
        byte_size=len(data),
        width=width,
        height=height,
        sha256=hashlib.sha256(data).hexdigest(),
        status="draft",
        created_by=user.id,
        metadata_json={},
    )
    db.add(asset)
    if shot_id:
        shot = await db.get(Shot, shot_id)
        if shot is not None:
            shot.reference_asset_ids = [*dict.fromkeys([*(shot.reference_asset_ids or []), asset_id])]
    if visual_profile is not None:
        visual_profile.reference_asset_ids = [*dict.fromkeys([*(visual_profile.reference_asset_ids or []), asset_id])]
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        target.unlink(missing_ok=True)
        raise
    await db.refresh(asset)
    return _asset_out(asset)


@router.patch("/assets/{asset_id}", response_model=ProductionAssetOut)
async def update_production_asset(
    asset_id: str,
    payload: ProductionAssetPatch,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    asset = await require_asset(asset_id, db, user, ProjectPermission.MANAGE_OUTLINE)
    asset.status = payload.status
    asset.rejection_reason = payload.rejection_reason if payload.status == "rejected" else None
    if asset.shot_id is not None:
        shot = await db.get(Shot, asset.shot_id)
        if shot is not None:
            references = shot.reference_asset_ids or []
            if payload.status == "rejected":
                shot.reference_asset_ids = [item for item in references if item != asset.id]
            elif payload.status == "approved" and asset.id not in references:
                shot.reference_asset_ids = [*references, asset.id]
    if asset.visual_profile_id is not None:
        profile = await db.get(VisualProfile, asset.visual_profile_id)
        if profile is not None:
            references = profile.reference_asset_ids or []
            if payload.status == "rejected":
                profile.reference_asset_ids = [item for item in references if item != asset.id]
            elif payload.status == "approved" and asset.id not in references:
                profile.reference_asset_ids = [*references, asset.id]
    await db.commit()
    await db.refresh(asset)
    return _asset_out(asset)


@router.get("/assets/{asset_id}/content")
async def get_production_asset_content(
    asset_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    asset = await require_asset(asset_id, db, user)
    try:
        path = asset_path(asset.storage_key)
    except InvalidProductionImageError as error:
        raise HTTPException(status_code=404, detail="素材不存在") from error
    if not path.is_file():
        raise HTTPException(status_code=404, detail="素材文件不存在")
    return FileResponse(path, media_type=asset.mime_type, filename=asset.original_filename)


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
    result = await db.execute(select(Scene).where(Scene.episode_id == episode_id).order_by(Scene.order, Scene.id))
    return result.scalars().all()


@router.put("/episodes/{episode_id}/scenes/order", response_model=list[SceneOut])
async def reorder_scenes(
    episode_id: str,
    payload: ReorderRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await require_episode(episode_id, db, user, ProjectPermission.MANAGE_OUTLINE)
    rows = (await db.execute(select(Scene).where(Scene.episode_id == episode_id).with_for_update())).scalars().all()
    by_id = {row.id: row for row in rows}
    if len(payload.ids) != len(rows) or len(set(payload.ids)) != len(payload.ids) or set(payload.ids) != set(by_id):
        raise HTTPException(status_code=422, detail={"code": "invalid_scene_order"})
    for order, scene_id in enumerate(payload.ids, start=1):
        by_id[scene_id].order = order
    await db.commit()
    return [by_id[scene_id] for scene_id in payload.ids]


@router.get("/episodes/{episode_id}/production-package")
async def get_production_package(
    episode_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Export a versioned, reviewable storyboard manifest, never the source prose."""
    episode = await require_episode(episode_id, db, user, ProjectPermission.EXPORT)
    adaptation = await db.get(Adaptation, episode.adaptation_id)
    chapter_rows = await db.execute(
        select(Chapter.id, Chapter.title).where(
            Chapter.project_id == adaptation.project_id,
            Chapter.id.in_(episode.source_chapter_ids),
            Chapter.deleted_at.is_(None),
        )
    )
    chapter_titles = dict(chapter_rows.all())
    scenes = (await db.execute(
        select(Scene).where(Scene.episode_id == episode_id).order_by(Scene.order, Scene.id)
    )).scalars().all()
    scene_ids = [scene.id for scene in scenes]
    shots = (await db.execute(
        select(Shot).where(Shot.scene_id.in_(scene_ids)).order_by(Shot.order, Shot.id)
    )).scalars().all() if scene_ids else []
    shots_by_scene: dict[str, list[Shot]] = {scene_id: [] for scene_id in scene_ids}
    for shot in shots:
        shots_by_scene[shot.scene_id].append(shot)
    character_ids = set().union(*(set(scene.character_entry_ids) for scene in scenes)) if scenes else set()
    profiles = (await db.execute(
        select(VisualProfile).where(
            VisualProfile.adaptation_id == adaptation.id,
            VisualProfile.codex_entry_id.in_(character_ids),
        ).order_by(VisualProfile.codex_entry_id)
    )).scalars().all() if character_ids else []
    profiles_by_character = {profile.codex_entry_id: profile for profile in profiles}
    referenced_asset_ids = {
        asset_id
        for shot in shots
        for asset_id in (shot.reference_asset_ids or [])
    }
    referenced_asset_ids.update(
        asset_id
        for profile in profiles
        for asset_id in (profile.reference_asset_ids or [])
    )
    asset_rows = (await db.execute(
        select(ProductionAsset).where(
            ProductionAsset.adaptation_id == adaptation.id,
            ProductionAsset.id.in_(referenced_asset_ids),
        ).order_by(ProductionAsset.created_at, ProductionAsset.id)
    )).scalars().all() if referenced_asset_ids else []
    assets_by_id = {asset.id: asset for asset in asset_rows}

    issues = []

    def issue(code: str, entity_type: str, entity_id: str, message: str, severity: str = "blocking") -> None:
        issues.append({
            "code": code, "severity": severity, "entity_type": entity_type,
            "entity_id": entity_id, "message": message,
        })

    if not scenes:
        issue("no_scenes", "episode", episode.id, "本集还没有场景")
    for chapter_id in episode.source_chapter_ids:
        if chapter_id not in chapter_titles:
            issue("source_chapter_missing", "episode", episode.id, f"来源章节 {chapter_id} 已不可用")
    for character_id in sorted(character_ids):
        profile = profiles_by_character.get(character_id)
        if not profile:
            issue("visual_profile_missing", "character", character_id, "出场人物缺少视觉档案")
        else:
            if not profile.locked:
                issue("visual_profile_unlocked", "character", character_id, "出场人物视觉档案尚未锁定")
            if not profile.appearance.strip():
                issue("visual_profile_appearance_missing", "character", character_id, "人物视觉档案缺少外观锚点")
    for asset_id in sorted(referenced_asset_ids):
        asset = assets_by_id.get(asset_id)
        if not asset:
            issue("asset_reference_missing", "shot", asset_id, "镜头引用的画面素材不存在")
        elif asset.status != "approved":
            issue("asset_unapproved", "shot", asset.id, "镜头引用的画面素材尚未确认")

    scene_data = []
    total_duration = 0
    for scene in scenes:
        scene_shots = shots_by_scene[scene.id]
        if not scene_shots:
            issue("no_shots", "scene", scene.id, "场景还没有镜头")
        shot_data = []
        for shot in scene_shots:
            total_duration += shot.duration_target
            if shot.status != "approved":
                issue("shot_unapproved", "shot", shot.id, "镜头尚未确认")
            if not shot.visual_prompt.strip():
                issue("visual_prompt_missing", "shot", shot.id, "镜头缺少画面提示词")
            shot_data.append({
                "id": shot.id, "order": shot.order, "shot_type": shot.shot_type,
                "camera": shot.camera, "duration_target": shot.duration_target,
                "action": shot.action, "dialogue": shot.dialogue, "narration": shot.narration,
                "visual_prompt": shot.visual_prompt, "reference_asset_ids": shot.reference_asset_ids,
                "status": shot.status,
            })
        scene_data.append({
            "id": scene.id, "order": scene.order, "purpose": scene.purpose,
            "summary": scene.summary, "time_anchor": scene.time_anchor,
            "location_entry_id": scene.location_entry_id,
            "character_entry_ids": scene.character_entry_ids, "shots": shot_data,
        })
    tolerance = max(5, round(episode.target_duration * 0.2))
    if shots and abs(total_duration - episode.target_duration) > tolerance:
        issue("duration_mismatch", "episode", episode.id, "镜头时长合计与目标时长偏差过大", "warning")

    style = adaptation.style_profile or {}
    return {
        "schema_version": 1,
        "project_id": adaptation.project_id,
        "adaptation": {
            "id": adaptation.id, "title": adaptation.title,
            "aspect_ratio": adaptation.aspect_ratio,
            "style_profile": {key: style[key] for key in ("label", "description") if isinstance(style.get(key), str)},
        },
        "episode": {
            "id": episode.id, "number": episode.number, "title": episode.title,
            "target_duration": episode.target_duration, "status": episode.status,
        },
        "source_chapters": [
            {"id": chapter_id, "title": chapter_titles.get(chapter_id)}
            for chapter_id in episode.source_chapter_ids
        ],
        "visual_profiles": [
            {
                "id": profile.id, "codex_entry_id": profile.codex_entry_id,
                "display_name": profile.display_name, "version": profile.version,
                "locked": profile.locked, "style": profile.style,
                "appearance": profile.appearance, "costume": profile.costume,
                "palette": profile.palette, "reference_asset_ids": profile.reference_asset_ids,
            }
            for profile in profiles
        ],
        "assets": [
            {
                "id": asset.id, "episode_id": asset.episode_id, "shot_id": asset.shot_id,
                "visual_profile_id": asset.visual_profile_id,
                "kind": asset.kind, "original_filename": asset.original_filename,
                "mime_type": asset.mime_type, "byte_size": asset.byte_size,
                "width": asset.width, "height": asset.height, "sha256": asset.sha256,
                "status": asset.status, "content_url": f"/assets/{asset.id}/content",
            }
            for asset in asset_rows
        ],
        "scenes": scene_data,
        "readiness": {
            "ready": not any(item["severity"] == "blocking" for item in issues),
            "total_duration": total_duration,
            "target_duration": episode.target_duration,
            "issues": issues,
        },
    }


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
    result = await db.execute(select(Shot).where(Shot.scene_id == scene_id).order_by(Shot.order, Shot.id))
    return result.scalars().all()


@router.put("/scenes/{scene_id}/shots/order", response_model=list[ShotOut])
async def reorder_shots(
    scene_id: str,
    payload: ReorderRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await require_scene(scene_id, db, user, ProjectPermission.MANAGE_OUTLINE)
    rows = (await db.execute(select(Shot).where(Shot.scene_id == scene_id).with_for_update())).scalars().all()
    by_id = {row.id: row for row in rows}
    if len(payload.ids) != len(rows) or len(set(payload.ids)) != len(payload.ids) or set(payload.ids) != set(by_id):
        raise HTTPException(status_code=422, detail={"code": "invalid_shot_order"})
    for order, shot_id in enumerate(payload.ids, start=1):
        by_id[shot_id].order = order
    await db.commit()
    return [by_id[shot_id] for shot_id in payload.ids]


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
