"""作者任务中心：聚合现有异步记录，不引入第二套任务状态表。

Celery 本身的结果后端并不是作者可见的业务数据源。这里读取已经持久化的
草稿、一致性运行、向量任务和 outbox 事件，并把每条记录的重试入口描述给
客户端。这样页面展示的是数据库事实，而不是根据轮询时间猜测的状态。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from db.models_consistency import OutboxEvent
from db.models_consistency_extended import ConsistencyRun
from db.models_core import Chapter, Project, User
from db.models_embedding import CodexEmbeddingJob
from db.models_org import OrgMember
from db.models_usage import GenerationDraft
from db.session import get_db

router = APIRouter(tags=["任务中心"])

TaskState = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class TaskRetry(BaseModel):
    """已有安全重试入口；客户端可以直接调用 method/path/body。"""

    method: Literal["POST"] = "POST"
    path: str
    body: dict[str, Any] | None = None
    label: str


class TaskItem(BaseModel):
    id: str
    kind: Literal["generation", "consistency", "embedding", "outbox"]
    title: str
    project_id: str
    project_title: str
    chapter_id: str | None = None
    chapter_index: int | None = None
    chapter_title: str | None = None
    state: TaskState
    source_status: str
    progress: int | None = Field(default=None, ge=0, le=100)
    progress_label: str | None = None
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    open_path: str | None = None
    retry: TaskRetry | None = None


class TaskCounts(BaseModel):
    total: int
    queued: int
    running: int
    succeeded: int
    failed: int
    cancelled: int


class TaskOverviewResponse(BaseModel):
    generated_at: str
    counts: TaskCounts
    items: list[TaskItem]
    limit: int
    has_more: bool


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


_SECRET_PATTERNS = (
    (re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"), r"\1[redacted]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"), "[redacted-key]"),
)


def _public_error(value: str | None) -> str | None:
    if not value:
        return None
    result = value.replace("\r", " ").replace("\n", " ").strip()
    for pattern, replacement in _SECRET_PATTERNS:
        result = pattern.sub(replacement, result)
    return result[:500] or None


def _path_segment(value: str) -> str:
    return quote(value, safe="")


def _chapter_context(
    project_id: str,
    chapter_id: str | None,
    projects: dict[str, Project],
    chapters: dict[str, Chapter],
) -> tuple[str, str | None, int | None, str | None]:
    project = projects[project_id]
    chapter = chapters.get(chapter_id) if chapter_id else None
    return (
        project.title,
        chapter.id if chapter else None,
        chapter.idx if chapter else None,
        chapter.title if chapter else None,
    )


def _consistency_retry(run: ConsistencyRun) -> TaskRetry:
    return TaskRetry(
        path="/consistency/scan",
        body={"chapter_id": run.chapter_id, "body_rev": run.body_rev, "trigger": "manual_scan"},
        label="重新扫描",
    )


def _embedding_retry(project_id: str) -> TaskRetry:
    return TaskRetry(
        path=f"/projects/{_path_segment(project_id)}/chapter-chunks/reindex",
        label="重建正文索引",
    )


def _outbox_retry(event: OutboxEvent, project_id: str) -> TaskRetry | None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    chapter_id = payload.get("chapter_id")
    body_rev = payload.get("body_rev")
    if event.topic == "consistency.manual_scan" and isinstance(chapter_id, str) and isinstance(body_rev, int):
        return TaskRetry(
            path="/consistency/scan",
            body={"chapter_id": chapter_id, "body_rev": body_rev, "trigger": "manual_scan"},
            label="重新扫描",
        )
    if event.topic in {"chapter.chunk_embedding_requested", "chapter.chunk_reindex_requested"}:
        return _embedding_retry(project_id)
    return None


def _draft_retry(draft: GenerationDraft) -> TaskRetry | None:
    if draft.status != "failed" or not (draft.content_text or "").strip():
        return None
    summary = draft.request_summary if isinstance(draft.request_summary, dict) else {}
    target_words = summary.get("targetWords", summary.get("target_words", 1000))
    model = summary.get("model", "basic")
    if not isinstance(target_words, int):
        target_words = 1000
    if not isinstance(model, str) or model not in {"basic", "advanced"}:
        model = "basic"
    return TaskRetry(
        path=f"/generate/drafts/{_path_segment(draft.id)}/continue",
        body={"targetWords": max(200, min(20000, target_words)), "model": model},
        label="继续生成",
    )


def _phase_label(value: str) -> str:
    return {
        "pending": "排队",
        "running": "运行中",
        "succeeded": "完成",
        "failed": "失败",
    }.get(value, value)


def _task_sort_key(item: TaskItem) -> str:
    return item.updated_at


async def _visible_projects(user: User, db: AsyncSession) -> dict[str, Project]:
    memberships = select(OrgMember.org_id).where(OrgMember.user_id == user.id)
    rows = await db.scalars(
        select(Project).where(or_(Project.owner_id == user.id, Project.org_id.in_(memberships)))
    )
    return {project.id: project for project in rows.all()}


@router.get("/overview", response_model=TaskOverviewResponse)
async def get_task_overview(
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaskOverviewResponse:
    """返回当前用户可见作品的任务活动及可重试入口。

    只读查询不会把 Celery 的瞬时结果写回业务表；尚未持久化的 broker 任务
    因此不会被伪造为“已完成”。
    """
    projects = await _visible_projects(user, db)
    if not projects:
        empty = TaskCounts(total=0, queued=0, running=0, succeeded=0, failed=0, cancelled=0)
        return TaskOverviewResponse(
            generated_at=_iso(datetime.now(UTC)) or "",
            counts=empty,
            items=[],
            limit=limit,
            has_more=False,
        )
    project_ids = list(projects)
    chapters = {
        chapter.id: chapter
        for chapter in (
            await db.scalars(select(Chapter).where(Chapter.project_id.in_(project_ids)))
        ).all()
    }
    items: list[TaskItem] = []

    drafts = (
        await db.scalars(
            select(GenerationDraft)
            .where(GenerationDraft.project_id.in_(project_ids))
            .order_by(GenerationDraft.updated_at.desc(), GenerationDraft.id.desc())
            .limit(limit + 1)
        )
    ).all()
    for draft in drafts:
        project_title, chapter_id, chapter_index, chapter_title = _chapter_context(
            draft.project_id, draft.chapter_id, projects, chapters
        )
        summary = draft.request_summary if isinstance(draft.request_summary, dict) else {}
        target = summary.get("targetWords", summary.get("target_words"))
        progress = None
        if isinstance(target, int) and target > 0:
            progress = min(100, round((draft.generated_words / target) * 100))
        state: TaskState = {
            "streaming": "running",
            "ready": "succeeded",
            "accepted": "succeeded",
            "failed": "failed",
            "rejected": "cancelled",
        }.get(draft.status, "running")  # type: ignore[assignment]
        if draft.status in {"ready", "accepted"}:
            progress = 100
        items.append(
            TaskItem(
                id=f"generation:{draft.id}",
                kind="generation",
                title="正文生成" if draft.kind == "chapter" else "段落生成",
                project_id=draft.project_id,
                project_title=project_title,
                chapter_id=chapter_id,
                chapter_index=chapter_index,
                chapter_title=chapter_title,
                state=state,
                source_status=draft.status,
                progress=progress,
                progress_label=(f"{draft.generated_words:,} 字" if draft.generated_words else None),
                created_at=_iso(draft.created_at) or "",
                updated_at=_iso(draft.updated_at) or _iso(draft.created_at) or "",
                finished_at=_iso(draft.accepted_at or draft.rejected_at),
                error_code=draft.error_code,
                open_path=(
                    f"/projects/{_path_segment(draft.project_id)}/write"
                    + (f"?chapter={_path_segment(draft.chapter_id)}" if draft.chapter_id else "")
                ),
                retry=_draft_retry(draft),
            )
        )

    runs = (
        await db.scalars(
            select(ConsistencyRun)
            .where(ConsistencyRun.project_id.in_(project_ids))
            .order_by(ConsistencyRun.updated_at.desc(), ConsistencyRun.id.desc())
            .limit(limit + 1)
        )
    ).all()
    for run in runs:
        project_title, chapter_id, chapter_index, chapter_title = _chapter_context(
            run.project_id, run.chapter_id, projects, chapters
        )
        state: TaskState = (
            "queued" if run.status == "pending" else
            "running" if run.status in {"extracting", "summarizing", "scanning"} else
            "succeeded" if run.status == "completed" else "failed"
        )
        phases = [run.extract_state, run.summary_state, run.scan_state]
        progress = round(sum(phase == "succeeded" for phase in phases) / 3 * 100)
        items.append(
            TaskItem(
                id=f"consistency:{run.id}",
                kind="consistency",
                title="一致性扫描",
                project_id=run.project_id,
                project_title=project_title,
                chapter_id=chapter_id,
                chapter_index=chapter_index,
                chapter_title=chapter_title,
                state=state,
                source_status=run.status,
                progress=progress,
                progress_label=(
                    f"抽取 {_phase_label(run.extract_state)} · "
                    f"摘要 {_phase_label(run.summary_state)} · "
                    f"扫描 {_phase_label(run.scan_state)}"
                ),
                created_at=_iso(run.created_at) or "",
                updated_at=_iso(run.updated_at) or _iso(run.created_at) or "",
                started_at=_iso(run.started_at),
                finished_at=_iso(run.finished_at),
                error_code=run.error_code,
                error_detail=_public_error(run.error_detail),
                open_path=(
                    f"/projects/{_path_segment(run.project_id)}/guard"
                    + (f"?chapter={_path_segment(run.chapter_id)}" if run.chapter_id else "")
                ),
                retry=_consistency_retry(run) if state == "failed" else None,
            )
        )

    embeddings = (
        await db.scalars(
            select(CodexEmbeddingJob)
            .where(CodexEmbeddingJob.project_id.in_(project_ids))
            .order_by(CodexEmbeddingJob.updated_at.desc(), CodexEmbeddingJob.project_id)
            .limit(limit + 1)
        )
    ).all()
    for job in embeddings:
        state: TaskState = {
            "queued": "queued", "retrying": "queued", "running": "running",
            "succeeded": "succeeded", "dead_letter": "failed",
        }.get(job.status, "queued")  # type: ignore[assignment]
        total = job.embedded_count + job.remaining_count
        progress = min(100, round(job.embedded_count / total * 100)) if total else (100 if state == "succeeded" else 0)
        project_title = projects[job.project_id].title
        items.append(
            TaskItem(
                id=f"embedding:{job.project_id}",
                kind="embedding",
                title="正文语义索引",
                project_id=job.project_id,
                project_title=project_title,
                state=state,
                source_status=job.status,
                progress=progress,
                progress_label=f"{job.embedded_count:,} / {total:,} 段" if total else None,
                created_at=_iso(job.created_at) or "",
                updated_at=_iso(job.updated_at) or _iso(job.created_at) or "",
                started_at=_iso(job.started_at),
                finished_at=_iso(job.completed_at or job.exhausted_at),
                error_code=job.error_code,
                error_detail=_public_error(job.last_error),
                open_path=f"/projects/{_path_segment(job.project_id)}/codex",
                retry=_embedding_retry(job.project_id) if state == "failed" else None,
            )
        )

    outbox_rows = (
        await db.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.status.in_(("pending", "dispatching", "failed", "dead_letter", "sent")))
            .order_by(OutboxEvent.updated_at.desc(), OutboxEvent.id.desc())
            .limit(limit * 3 + 1)
        )
    ).all()
    for event in outbox_rows:
        payload = event.payload if isinstance(event.payload, dict) else {}
        project_id = payload.get("project_id")
        if not isinstance(project_id, str) or project_id not in projects:
            continue
        chapter_id = payload.get("chapter_id") if isinstance(payload.get("chapter_id"), str) else None
        project_title, chapter_id, chapter_index, chapter_title = _chapter_context(
            project_id, chapter_id, projects, chapters
        )
        state: TaskState = (
            "queued" if event.status == "pending" else
            "running" if event.status == "dispatching" else
            "succeeded" if event.status == "sent" else "failed"
        )
        retry = _outbox_retry(event, project_id) if state == "failed" else None
        items.append(
            TaskItem(
                id=f"outbox:{event.id}",
                kind="outbox",
                title=f"后台任务 · {event.topic}",
                project_id=project_id,
                project_title=project_title,
                chapter_id=chapter_id,
                chapter_index=chapter_index,
                chapter_title=chapter_title,
                state=state,
                source_status=event.status,
                progress=None,
                progress_label=("等待投递" if state == "queued" else "已投递" if state == "succeeded" else None),
                created_at=_iso(event.created_at) or "",
                updated_at=_iso(event.updated_at) or _iso(event.created_at) or "",
                error_detail=_public_error(event.last_error),
                open_path=(
                    f"/projects/{_path_segment(project_id)}/guard"
                    if event.topic.startswith("consistency")
                    else f"/projects/{_path_segment(project_id)}/codex"
                ),
                retry=retry,
            )
        )

    items.sort(key=_task_sort_key, reverse=True)
    has_more = len(items) > limit
    count_values = {state: 0 for state in ("queued", "running", "succeeded", "failed", "cancelled")}
    for item in items:
        count_values[item.state] += 1
    total = len(items)
    items = items[:limit]
    return TaskOverviewResponse(
        generated_at=_iso(datetime.now(UTC)) or "",
        counts=TaskCounts(total=total, **count_values),
        items=items,
        limit=limit,
        has_more=has_more,
    )


__all__ = ["router"]
