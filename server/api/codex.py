"""
设定库 CRUD API - 条目创建/更新、别名增删、授权回填端点。

事务形状（见 services.codex 的模块文档）：每个写端点都是**两次 commit**。
第一次提交作者的改动与标脏；提交之后才调 embedding 网关，成功再提交向量。
网关失败不让请求失败 —— 响应里 `embedding_status="deferred"` 表示条目已落库、
向量待补，由 tasks.codex 的回填任务带指数退避重试。

鉴权：所有端点都过 verify_project_access（owner 或 org 成员），回填端点同样如此
—— 全项目回填会打满 embedding 网关配额，不能任人触发。
"""
import secrets
from datetime import datetime
from typing import Annotated, Literal, Optional, get_args

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import ProjectPermission, get_current_user, verify_project_permission
from db.models_codex import CODEX_STATUSES, CodexAlias, CodexEntry, CodexRef
from db.models_core import Chapter, Project, User
from db.models_embedding import CodexEmbeddingJob
from db.models_guard import Foreshadow
from db.session import get_db
from services.codex import (
    add_alias,
    count_stale_entries,
    create_entry,
    refresh_embedding_if_stale,
    remove_alias,
    replace_aliases,
    update_entry,
)
from services.codex_embedding import embed_missing_codex_entries
from services.embedding import GatewayEmbeddingProvider
from services.embedding_jobs import fail_job, lock_job, queue_job
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
AliasValue = Annotated[str, Field(min_length=1, max_length=200)]


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
    aliases: list[AliasValue] = Field(default_factory=list)
    attrs: dict = Field(default_factory=dict)
    resident: bool = False
    status: CodexStatus = "confirmed"
    planted_at: str | None = None
    expected_by: str | None = None
    foreshadow_resolved: bool = False
    resolved_at: str | None = None


class UpdateEntryRequest(BaseModel):
    """所有字段可选；未提供的字段不动（exclude_unset）。"""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    kind: Optional[CodexKind] = None
    description: Optional[str] = None
    attrs: Optional[dict] = None
    resident: Optional[bool] = None
    status: Optional[CodexStatus] = None
    aliases: Optional[list[AliasValue]] = None
    planted_at: str | None = None
    expected_by: str | None = None
    foreshadow_resolved: bool | None = None
    resolved_at: str | None = None


class AliasRequest(BaseModel):
    alias: AliasValue


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
    ref_chapters: list[str]
    conflicts: list[str]
    planted_at: str | None
    expected_by: str | None
    foreshadow_resolved: bool
    resolved_at: str | None
    embedding_status: EmbeddingStatus


class AliasMutationResponse(BaseModel):
    alias: str
    changed: bool
    embedding_status: EmbeddingStatus


class BackfillResponse(BaseModel):
    project_id: str
    embedded_count: int
    remaining_count: int


class EmbeddingJobResponse(BaseModel):
    project_id: str
    status: Literal["ready", "pending", "queued", "running", "retrying", "dead_letter"]
    total_count: int
    fresh_count: int
    remaining_count: int
    attempts: int
    dispatch_attempts: int
    task_id: str | None
    error_code: str | None
    last_error: str | None
    last_attempt_at: datetime | None
    exhausted_at: datetime | None
    can_retry: bool


def dispatch_embedding_backfill(project_id: str, batch_size: int) -> str:
    from celery_app import celery_app

    return celery_app.send_task(
        "codex.backfill_embeddings",
        args=(project_id, batch_size),
    ).id


async def _embedding_job_response(
    db: AsyncSession,
    project_id: str,
    job: CodexEmbeddingJob | None = None,
) -> EmbeddingJobResponse:
    total = int((await db.execute(
        select(func.count(CodexEntry.id)).where(CodexEntry.project_id == project_id)
    )).scalar_one())
    remaining = await count_stale_entries(db, project_id)
    if job is None:
        job = await db.get(CodexEmbeddingJob, project_id)
    if remaining == 0:
        visible_status = "ready"
    elif job is None or job.status == "succeeded":
        visible_status = "pending"
    else:
        visible_status = job.status
    active = visible_status in {"queued", "running", "retrying"}
    show_failure = remaining > 0 and job is not None
    return EmbeddingJobResponse(
        project_id=project_id,
        status=visible_status,
        total_count=total,
        fresh_count=max(0, total - remaining),
        remaining_count=remaining,
        attempts=job.attempts if job else 0,
        dispatch_attempts=job.dispatch_attempts if job else 0,
        task_id=job.task_id if job else None,
        error_code=job.error_code if show_failure else None,
        last_error=job.last_error if show_failure else None,
        last_attempt_at=job.last_attempt_at if job else None,
        exhausted_at=job.exhausted_at if show_failure else None,
        can_retry=remaining > 0 and not active,
    )


async def _load_entry(db: AsyncSession, project_id: str, entry_id: str) -> CodexEntry:
    """按项目锁定条目，隔离跨项目访问并串行化同一条目的写入。"""
    result = await db.execute(
        select(CodexEntry).where(
            CodexEntry.id == entry_id, CodexEntry.project_id == project_id
        ).with_for_update()
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Codex entry not found")
    return entry


async def _load_foreshadow(db: AsyncSession, entry_id: str) -> Foreshadow | None:
    return (
        await db.execute(select(Foreshadow).where(Foreshadow.entry_id == entry_id))
    ).scalar_one_or_none()


async def _chapter_map(
    db: AsyncSession, project_id: str, chapter_ids: set[str]
) -> dict[str, Chapter]:
    if not chapter_ids:
        return {}
    chapters = list(
        (
            await db.execute(
                select(Chapter).where(
                    Chapter.project_id == project_id,
                    Chapter.id.in_(chapter_ids),
                    Chapter.deleted_at.is_(None),
                )
            )
        ).scalars()
    )
    found = {chapter.id: chapter for chapter in chapters}
    missing = sorted(chapter_ids - set(found))
    if missing:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_FORESHADOW_CHAPTER", "chapter_ids": missing},
        )
    return found


async def _sync_foreshadow(
    db: AsyncSession,
    entry: CodexEntry,
    *,
    planted_at: str,
    expected_by: str | None,
    resolved: bool,
    resolved_at: str | None,
) -> Foreshadow:
    chapter_ids = {planted_at}
    chapter_ids.update(value for value in (expected_by, resolved_at) if value)
    chapters = await _chapter_map(db, entry.project_id, chapter_ids)
    # Every row in this map passed the project_id filter in _chapter_map, so
    # order comparisons cannot cross tenant boundaries.
    planted_idx = chapters[planted_at].idx
    if expected_by and chapters[expected_by].idx < planted_idx:
        raise HTTPException(status_code=422, detail={"code": "FORESHADOW_EXPECTED_BEFORE_PLANTED"})
    if resolved and not resolved_at:
        raise HTTPException(status_code=422, detail={"code": "FORESHADOW_RESOLUTION_CHAPTER_REQUIRED"})
    if resolved_at and chapters[resolved_at].idx < planted_idx:
        raise HTTPException(status_code=422, detail={"code": "FORESHADOW_RESOLVED_BEFORE_PLANTED"})

    lifecycle = await _load_foreshadow(db, entry.id)
    if lifecycle is None:
        lifecycle = Foreshadow(
            id=f"fs_{secrets.token_hex(12)}",
            project_id=entry.project_id,
            entry_id=entry.id,
            planted_chapter_id=planted_at,
            expected_chapter_id=expected_by,
            description=entry.description,
            resolved=resolved,
            resolved_chapter_id=resolved_at if resolved else None,
        )
        db.add(lifecycle)
    else:
        lifecycle.planted_chapter_id = planted_at
        lifecycle.expected_chapter_id = expected_by
        lifecycle.description = entry.description
        lifecycle.resolved = resolved
        lifecycle.resolved_chapter_id = resolved_at if resolved else None
    # Keep legacy response/export fields synchronized while Foreshadow owns state.
    entry.planted_at = planted_at
    entry.expected_by = expected_by
    await db.flush()
    return lifecycle


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
    lifecycle = await _load_foreshadow(db, entry.id)
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
        ref_chapters=entry.ref_chapters,
        conflicts=entry.conflicts,
        planted_at=entry.planted_at,
        expected_by=entry.expected_by,
        foreshadow_resolved=lifecycle.resolved if lifecycle else False,
        resolved_at=lifecycle.resolved_chapter_id if lifecycle else None,
        embedding_status=embedding_status,
    )


@router.get("/{project_id}/entries", response_model=list[EntryResponse])
async def list_codex_entries(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出当前作品的设定，供写作工作台与 @ 引用共同使用。"""
    await verify_project_permission(project_id, ProjectPermission.VIEW, user, db)

    entries = list(
        (
            await db.execute(
                select(CodexEntry)
                .where(CodexEntry.project_id == project_id)
                .order_by(CodexEntry.kind, CodexEntry.name, CodexEntry.id)
            )
        )
        .scalars()
        .all()
    )
    if not entries:
        return []

    alias_rows = (
        await db.execute(
            select(CodexAlias.entry_id, CodexAlias.alias)
            .where(CodexAlias.entry_id.in_([entry.id for entry in entries]))
            .order_by(CodexAlias.entry_id, CodexAlias.alias)
        )
    ).all()
    aliases_by_entry: dict[str, list[str]] = {}
    for entry_id, alias in alias_rows:
        aliases_by_entry.setdefault(entry_id, []).append(alias)

    lifecycle_rows = list(
        (
            await db.execute(
                select(Foreshadow).where(Foreshadow.entry_id.in_([entry.id for entry in entries]))
            )
        ).scalars()
    )
    lifecycle_by_entry = {row.entry_id: row for row in lifecycle_rows}

    return [
        EntryResponse(
            id=entry.id,
            project_id=entry.project_id,
            kind=entry.kind,
            name=entry.name,
            description=entry.description,
            aliases=aliases_by_entry.get(entry.id, []),
            attrs=entry.attrs,
            resident=entry.resident,
            status=entry.status,
            ref_chapters=entry.ref_chapters,
            conflicts=entry.conflicts,
            planted_at=entry.planted_at,
            expected_by=entry.expected_by,
            foreshadow_resolved=(
                lifecycle_by_entry[entry.id].resolved if entry.id in lifecycle_by_entry else False
            ),
            resolved_at=(
                lifecycle_by_entry[entry.id].resolved_chapter_id
                if entry.id in lifecycle_by_entry else None
            ),
            embedding_status=(
                "fresh"
                if entry.embedding is not None and entry.embedding_text_hash is not None
                else "deferred"
            ),
        )
        for entry in entries
    ]


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
    await verify_project_permission(project_id, ProjectPermission.MANAGE_CODEX, user, db)

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
    if request.kind == "event":
        if not request.planted_at:
            raise HTTPException(status_code=422, detail={"code": "FORESHADOW_PLANTED_CHAPTER_REQUIRED"})
        await _sync_foreshadow(
            db,
            entry,
            planted_at=request.planted_at,
            expected_by=request.expected_by,
            resolved=request.foreshadow_resolved,
            resolved_at=request.resolved_at,
        )
    elif any((request.planted_at, request.expected_by, request.foreshadow_resolved, request.resolved_at)):
        raise HTTPException(status_code=422, detail={"code": "FORESHADOW_FIELDS_REQUIRE_EVENT_KIND"})
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
    await verify_project_permission(project_id, ProjectPermission.MANAGE_CODEX, user, db)
    entry = await _load_entry(db, project_id, entry_id)

    changes = request.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="No fields to update")

    aliases = changes.pop("aliases", None)
    lifecycle_changes = {
        field: changes.pop(field)
        for field in ("planted_at", "expected_by", "foreshadow_resolved", "resolved_at")
        if field in changes
    }
    target_kind = changes.get("kind", entry.kind)
    existing_lifecycle = await _load_foreshadow(db, entry.id)
    text_changed = await update_entry(db, entry, changes) if changes else False
    if aliases is not None:
        text_changed = await replace_aliases(db, entry, aliases) or text_changed
    if target_kind == "event":
        planted_at = lifecycle_changes.get(
            "planted_at",
            existing_lifecycle.planted_chapter_id if existing_lifecycle else entry.planted_at,
        )
        if not planted_at:
            raise HTTPException(status_code=422, detail={"code": "FORESHADOW_PLANTED_CHAPTER_REQUIRED"})
        expected_by = lifecycle_changes.get(
            "expected_by",
            existing_lifecycle.expected_chapter_id if existing_lifecycle else entry.expected_by,
        )
        resolved = lifecycle_changes.get(
            "foreshadow_resolved", existing_lifecycle.resolved if existing_lifecycle else False
        )
        resolved_at = lifecycle_changes.get(
            "resolved_at", existing_lifecycle.resolved_chapter_id if existing_lifecycle else None
        )
        await _sync_foreshadow(
            db,
            entry,
            planted_at=planted_at,
            expected_by=expected_by,
            resolved=resolved,
            resolved_at=resolved_at,
        )
    else:
        if lifecycle_changes:
            raise HTTPException(status_code=422, detail={"code": "FORESHADOW_FIELDS_REQUIRE_EVENT_KIND"})
        if existing_lifecycle:
            await db.delete(existing_lifecycle)
        entry.planted_at = None
        entry.expected_by = None
    await db.commit()

    embedding_status = await _settle_embedding(db, provider, entry, stale=text_changed)
    return await _entry_response(db, entry, embedding_status)


@router.delete(
    "/{project_id}/entries/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_codex_entry(
    project_id: str,
    entry_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """删除未使用的设定，或直接丢弃尚未确认的抽取候选。

    已确认且被正文引用的条目不能静默删除。否则正文里的显式引用虽然会由外键
    级联清理，作者却失去了这段引用原本指向谁的语义。待确认候选本来就是可丢弃
    的抽取结果，即使记录了来源章节也允许删除。_load_entry 的行锁还会与正文保存
    时外键检查取得的 key-share 锁互斥，避免检查引用后又并发插入新引用。
    """
    await verify_project_permission(project_id, ProjectPermission.MANAGE_CODEX, user, db)
    entry = await _load_entry(db, project_id, entry_id)

    reference_count = int(
        (
            await db.execute(
                select(func.count(CodexRef.id)).where(CodexRef.entry_id == entry.id)
            )
        ).scalar_one()
    )
    if entry.status == "confirmed" and (reference_count > 0 or entry.ref_chapters):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CODEX_ENTRY_IN_USE",
                "reference_count": max(reference_count, len(entry.ref_chapters)),
            },
        )

    await db.delete(entry)
    await db.commit()


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
    await verify_project_permission(project_id, ProjectPermission.MANAGE_CODEX, user, db)
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
    await verify_project_permission(project_id, ProjectPermission.MANAGE_CODEX, user, db)
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
    await verify_project_permission(project_id, ProjectPermission.MANAGE_CODEX, user, db)

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


@router.get("/{project_id}/embedding-status", response_model=EmbeddingJobResponse)
async def get_embedding_status(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EmbeddingJobResponse:
    """Return durable retry/dead-letter state plus current stale-entry counts."""
    await verify_project_permission(project_id, ProjectPermission.VIEW, user, db)
    return await _embedding_job_response(db, project_id)


@router.post(
    "/{project_id}/embedding-backfill",
    response_model=EmbeddingJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_embedding_backfill(
    project_id: str,
    batch_size: int = 32,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EmbeddingJobResponse:
    """Queue one observable backfill; concurrent clicks reuse the active job."""
    await verify_project_permission(project_id, ProjectPermission.MANAGE_CODEX, user, db)
    if batch_size <= 0:
        raise HTTPException(status_code=422, detail="batch_size must be positive")

    # Serialize first-time row creation and duplicate queue attempts on the project row.
    await db.execute(select(Project.id).where(Project.id == project_id).with_for_update())
    remaining = await count_stale_entries(db, project_id)
    job = await lock_job(db, project_id)
    if remaining == 0 or (job and job.status in {"queued", "running", "retrying"}):
        return await _embedding_job_response(db, project_id, job)

    job = await queue_job(db, project_id, remaining)
    await db.commit()
    try:
        task_id = dispatch_embedding_backfill(project_id, batch_size)
    except Exception as error:
        await fail_job(
            db,
            project_id,
            attempt=0,
            error=error,
            remaining_count=remaining,
            exhausted=True,
        )
        await db.commit()
        raise HTTPException(
            status_code=503,
            detail={"code": "EMBEDDING_QUEUE_UNAVAILABLE"},
        ) from error
    job = await lock_job(db, project_id)
    if job:
        job.task_id = task_id
    await db.commit()
    return await _embedding_job_response(db, project_id, job)
