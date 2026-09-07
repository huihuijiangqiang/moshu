"""Project-scoped writing utilities.

The utilities are intentionally small and deterministic. They provide fast
draft material without spending model credits, while saved map drafts live in
the project's existing story settings document so they travel with exports.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import ProjectPermission, get_current_user, verify_project_permission
from db.models_core import User
from db.session import get_db

router = APIRouter()

NameStyle = Literal["古典", "清冷", "明快", "异域"]
NameGender = Literal["女", "男", "不限"]
Terrain = Literal["山河", "群岛", "荒原"]


class NameRequest(BaseModel):
    style: NameStyle = "古典"
    gender: NameGender = "不限"
    seed: str = Field("春山", min_length=1, max_length=20)
    count: int = Field(6, ge=1, le=20)


class NameResponse(BaseModel):
    names: list[str]
    seed: str
    style: NameStyle
    gender: NameGender


class MapRegion(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)


class MapDraft(BaseModel):
    seed: str = Field("柳溪", min_length=1, max_length=20)
    terrain: Terrain = "山河"
    regions: list[MapRegion] = Field(default_factory=list, max_length=20)


class MapGenerateRequest(BaseModel):
    seed: str = Field("柳溪", min_length=1, max_length=20)
    terrain: Terrain = "山河"
    region_count: int = Field(7, ge=4, le=10)


class MapDraftResponse(MapDraft):
    project_id: str
    saved: bool = True
    updated_at: str | None = None


_FAMILY = ["许", "沈", "顾", "周", "陆", "谢", "裴", "苏", "林", "秦", "程", "姜"]
_FEMALE = ["知微", "照棠", "明昭", "云岫", "青禾", "令仪", "晚晴", "栖月", "南枝", "见山", "初霁", "绾宁"]
_MALE = ["砚川", "长庚", "景行", "怀瑾", "承安", "闻舟", "修远", "既白", "庭深", "昭野", "观澜", "行之"]
_NEUTRAL = ["知微", "砚川", "清和", "照野", "明川", "长宁", "栖迟", "怀远", "青衡", "听澜", "山止", "云开"]
_EXOTIC_FAMILY = ["阿", "伊", "洛", "赫", "塔", "乌", "赛", "迦"]
_EXOTIC_GIVEN = ["弥娅", "岚歌", "萨恩", "诺娅", "迦南", "维洛", "星遥", "阿岚"]
_REGION_NAMES = ["柳溪", "青溪县", "白沙渡", "南岭", "望潮港", "鹤鸣原", "长风关", "照雪城", "镜湖", "栖霞镇"]


def _index(seed: str, index: int, length: int) -> int:
    digest = hashlib.sha256(f"{seed}:{index}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % length


def _names(request: NameRequest) -> list[str]:
    surnames = _EXOTIC_FAMILY if request.style == "异域" else _FAMILY
    if request.style == "异域":
        given = _EXOTIC_GIVEN
    elif request.gender == "女":
        given = _FEMALE
    elif request.gender == "男":
        given = _MALE
    else:
        given = _NEUTRAL
    result: list[str] = []
    for offset in range(request.count * 3):
        value = surnames[_index(request.seed, offset, len(surnames))] + given[_index(request.seed, offset + 17, len(given))]
        if value not in result:
            result.append(value)
        if len(result) >= request.count:
            break
    return result


def _default_map(seed: str, terrain: Terrain, count: int) -> list[MapRegion]:
    count = max(4, min(10, count))
    regions: list[MapRegion] = []
    for index in range(count):
        angle = (index / count) * 6.283185307
        jitter = _index(seed, index, 17) - 8
        import math

        regions.append(MapRegion(
            name=_REGION_NAMES[index],
            x=round(50 + math.cos(angle) * (28 + jitter / 2), 2),
            y=round(48 + math.sin(angle) * (25 + jitter / 3), 2),
        ))
    return regions


@router.post("/{project_id}/tools/names", response_model=NameResponse)
async def generate_names(
    project_id: str,
    request: NameRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NameResponse:
    await verify_project_permission(project_id, ProjectPermission.VIEW, user, db)
    return NameResponse(names=_names(request), seed=request.seed, style=request.style, gender=request.gender)


@router.post("/{project_id}/tools/maps", response_model=MapDraftResponse)
async def generate_map(
    project_id: str,
    request: MapGenerateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MapDraftResponse:
    await verify_project_permission(project_id, ProjectPermission.VIEW, user, db)
    return MapDraftResponse(
        project_id=project_id,
        seed=request.seed,
        terrain=request.terrain,
        regions=_default_map(request.seed, request.terrain, request.region_count),
        saved=False,
    )


@router.get("/{project_id}/tools/map-draft", response_model=MapDraftResponse)
async def get_map_draft(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MapDraftResponse:
    project = await verify_project_permission(project_id, ProjectPermission.VIEW, user, db)
    raw = (project.story_settings or {}).get("tools", {}).get("map_draft")
    draft = MapDraft.model_validate(raw) if isinstance(raw, dict) else MapDraft(regions=_default_map("柳溪", "山河", 7))
    return MapDraftResponse(project_id=project_id, **draft.model_dump())


@router.put("/{project_id}/tools/map-draft", response_model=MapDraftResponse)
async def save_map_draft(
    project_id: str,
    request: MapDraft,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MapDraftResponse:
    project = await verify_project_permission(project_id, ProjectPermission.MANAGE_CODEX, user, db)
    settings = dict(project.story_settings or {})
    tools = dict(settings.get("tools") or {})
    tools["map_draft"] = request.model_dump()
    settings["tools"] = tools
    project.story_settings = settings
    await db.commit()
    await db.refresh(project)
    return MapDraftResponse(project_id=project_id, **request.model_dump())


@router.delete("/{project_id}/tools/map-draft", status_code=204)
async def delete_map_draft(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    project = await verify_project_permission(project_id, ProjectPermission.MANAGE_CODEX, user, db)
    settings = dict(project.story_settings or {})
    tools = dict(settings.get("tools") or {})
    tools.pop("map_draft", None)
    settings["tools"] = tools
    project.story_settings = settings
    await db.commit()
