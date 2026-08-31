"""
设定库 CRUD API - 条目创建/更新、别名增删、授权回填端点。

事务形状（见 services.codex 的模块文档）：每个写端点都是**两次 commit**。
第一次提交作者的改动与标脏；提交之后才调 embedding 网关，成功再提交向量。
网关失败不让请求失败 —— 响应里 `embedding_status="deferred"` 表示条目已落库、
向量待补，由 tasks.codex 的回填任务带指数退避重试。

鉴权：所有端点都过 verify_project_access（owner 或 org 成员），回填端点同样如此
—— 全项目回填会打满 embedding 网关配额，不能任人触发。
"""
from typing import Literal, Optional, get_args

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, verify_project_access
from db.models_codex import CODEX_STATUSES, CodexAlias, CodexEntry
from db.models_core import User
from db.session import get_db
from services.codex import (
    add_alias,
    count_stale_entries,
    create_entry,
    refresh_embedding_if_stale,
    remove_alias,
    update_entry,
)
from services.codex_embedding import embed_missing_codex_entries
from services.embedding import GatewayEmbeddingProvider
from services.providers import EmbeddingProvider

router = APIRouter()

#: CodexEntry.kind / status 的合法取值。用 Literal 让 FastAPI 直接以 422 拒绝
#: 非法值（与 api.consistency 的 ResolutionAction 同一套写法），数据库的
#: ck_codex_entry_status CHECK 约束是第二道防线。
CodexKind = Literal["character", "location", "item", "faction", "event", "rule"]
VALID_CODEX_KINDS: tuple[str, ...] = get_args(CodexKind)

CodexStatus = Literal["confirmed", "pending"]
VALID_CODEX_STATUSES: tuple[str, ...] = get_args(CodexStatus)

# Literal 无法用变量拼出来（类型注解要在导入期求值），所以这里在导入期核对它与
# db.models_codex 的取值表一致：加了状态却忘了改 API，作者就写不进新状态；反过来
# API 放开了而 CHECK 没放开，写入会在数据库层炸成 500。用 raise 而不是 assert ——
# python -O 会把 assert 整条去掉，而这道校验必须在生产环境同样生效。
if VALID_CODEX_STATUSES != CODEX_STATUSES:
    raise RuntimeError(
        f"api.codex 的 CodexStatus {VALID_CODEX_STATUSES} 与 "
        f"db.models_codex.CODEX_STATUSES {CODEX_STATUSES} 不一致"
    )

EmbeddingStatus = Literal["fresh", "updated", "deferred"]


def get_embedding_provider() -> EmbeddingProvider:
    """embedding provider 依赖 —— 测试通过 dependency_overrides 换成 mock。

    做成依赖而不是模块级单例：测试要能在不发网络请求的前提下走完整个端点，
    而 provider 直接 new 在 handler 里就没法替换。
    """
    return GatewayEmbeddingProvider()


class CreateEntryRequest(BaseModel):
    kind: CodexKind
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    attrs: dict = Field(default_factory=dict)
    resident: bool = False
    status: CodexStatus = "confirmed"


class UpdateEntryRequest(BaseModel):
    """所有字段可选；未提供的字段不动（exclude_unset）。"""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    kind: Optional[CodexKind] = None
    description: Optional[str] = None
    attrs: Optional[dict] = None
    resident: Optional[bool] = None
    status: Optional[CodexStatus] = None


class AliasRequest(BaseModel):
    alias: str = Field(..., min_length=1, max_length=200)


class EntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    kind: str
    name: str
    description: str
    aliases: list[str]
    attrs: dict
    resident: bool
    status: str
    embedding_status: EmbeddingStatus


class AliasMutationResponse(BaseModel):
    alias: str
    changed: bool
    embedding_status: EmbeddingStatus


class BackfillResponse(BaseModel):
    project_id: str
    embedded_count: int
    remaining_count: int


async def _load_entry(db: AsyncSession, project_id: str, entry_id: str) -> CodexEntry:
    """按项目取条目 —— project_id 参与过滤，避免跨项目按 id 直取。"""
    result = await db.execute(
        select(CodexEntry).where(
            CodexEntry.id == entry_id, CodexEntry.project_id == project_id
        )
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Codex entry not found")
    return entry


async def _settle_embedding(
    db: AsyncSession, provider: EmbeddingProvider, entry: CodexEntry, *, stale: bool
) -> str:
    """第二段事务：补向量并提交。

    即使 stale=False（可检索文本没变），仍需调 refresh_embedding_if_stale 确认
    条目真的 fresh：条目可能在之前的网关失败后留在 deferred 状态，改 attrs 不使其
    变脏，但也不该谎报 fresh。refresh_embedding_if_stale 会检查哈希：真正 fresh 时
    不调网关，deferred 时尝试补齐。
    """
    embedding_status = await refresh_embedding_if_stale(db, provider, entry)
    await db.commit()
    return embedding_status


async def _entry_response(
    db: AsyncSession, entry: CodexEntry, embedding_status: str
) -> EntryResponse:
    aliases = list(
        (
            await db.execute(
                select(CodexAlias.alias)
                .where(CodexAlias.entry_id == entry.id)
                .order_by(CodexAlias.alias)
            )
        )
        .scalars()
        .all()
    )
    return EntryResponse(
        id=entry.id,
        project_id=entry.project_id,
        kind=entry.kind,
        name=entry.name,
        description=entry.description,
        aliases=aliases,
        attrs=entry.attrs,
        resident=entry.resident,
        status=entry.status,
        embedding_status=embedding_status,
    )


@router.post(
    "/{project_id}/entries",
    response_model=EntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_codex_entry(
    project_id: str,
    request: CreateEntryRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    provider: EmbeddingProvider = Depends(get_embedding_provider),
):
    """创建条目及其别名，随后补齐 embedding。"""
    await verify_project_access(project_id, user, db)

    entry = await create_entry(
        db,
        project_id=project_id,
        kind=request.kind,
        name=request.name,
        description=request.description,
        aliases=request.aliases,
        attrs=request.attrs,
        resident=request.resident,
        status=request.status,
    )
    await db.commit()  # 第一段：条目 + 标脏落库，网关还没被碰过

    embedding_status = await _settle_embedding(db, provider, entry, stale=True)
    return await _entry_response(db, entry, embedding_status)


@router.patch("/{project_id}/entries/{entry_id}", response_model=EntryResponse)
async def update_codex_entry(
    project_id: str,
    entry_id: str,
    request: UpdateEntryRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    provider: EmbeddingProvider = Depends(get_embedding_provider),
):
    """更新名称/类型/描述等字段；只有可检索文本变化才重算向量。"""
    await verify_project_access(project_id, user, db)
    entry = await _load_entry(db, project_id, entry_id)

    changes = request.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="No fields to update")

    text_changed = await update_entry(db, entry, changes)
    await db.commit()

    embedding_status = await _settle_embedding(db, provider, entry, stale=text_changed)
    return await _entry_response(db, entry, embedding_status)


@router.post(
    "/{project_id}/entries/{entry_id}/aliases",
    response_model=AliasMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_codex_alias(
    project_id: str,
    entry_id: str,
    request: AliasRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    provider: EmbeddingProvider = Depends(get_embedding_provider),
):
    """添加别名。重复添加同一别名幂等：不加行，也不重算向量。"""
    await verify_project_access(project_id, user, db)
    entry = await _load_entry(db, project_id, entry_id)

    added = await add_alias(db, entry, request.alias)
    await db.commit()

    embedding_status = await _settle_embedding(db, provider, entry, stale=added)
    return AliasMutationResponse(
        alias=request.alias, changed=added, embedding_status=embedding_status
    )


@router.delete(
    "/{project_id}/entries/{entry_id}/aliases", response_model=AliasMutationResponse
)
async def remove_codex_alias(
    project_id: str,
    entry_id: str,
    request: AliasRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    provider: EmbeddingProvider = Depends(get_embedding_provider),
):
    """删除别名。别名本来不存在时不重算向量。"""
    await verify_project_access(project_id, user, db)
    entry = await _load_entry(db, project_id, entry_id)

    removed = await remove_alias(db, entry, request.alias)
    await db.commit()

    embedding_status = await _settle_embedding(db, provider, entry, stale=removed)
    return AliasMutationResponse(
        alias=request.alias, changed=removed, embedding_status=embedding_status
    )


@router.post("/{project_id}/backfill-embeddings", response_model=BackfillResponse)
async def backfill_codex_embeddings(
    project_id: str,
    batch_size: int = 32,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    provider: EmbeddingProvider = Depends(get_embedding_provider),
):
    """在请求内同步回填本项目所有待重算的向量（受项目权限保护）。

    `remaining_count` 是本次回填成功后的待重算快照，而非持续可查询的失败状态。
    当前无独立 GET 项目状态端点、无 dead-letter 标记，无法持续监控任务耗尽重试
    的失败。带重试的异步版本见 tasks.codex.backfill_codex_embeddings_task。
    """
    await verify_project_access(project_id, user, db)

    if batch_size <= 0:
        raise HTTPException(status_code=422, detail="batch_size must be positive")

    embedded = await embed_missing_codex_entries(
        db, provider, project_id=project_id, batch_size=batch_size, commit_each_batch=True
    )
    await db.commit()

    return BackfillResponse(
        project_id=project_id,
        embedded_count=embedded,
        remaining_count=await count_stale_entries(db, project_id),
    )
