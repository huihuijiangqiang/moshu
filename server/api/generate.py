"""Authenticated chapter and inline novel generation endpoints."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import AsyncIterator, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import (
    ProjectPermission,
    get_current_user,
    verify_chapter_assignment,
    verify_project_permission,
)
from config import settings
from db.models_core import Chapter, ChapterBody, Project, User
from db.models_usage import GenerationDraft, GenerationRun
from db.session import get_db
from memory.assembler import ContextBudgetPolicy
from memory.tokenizer import tokenizer
from services.generation import (
    GenerationGateway,
    GenerationProviderError,
    GenerationRoute,
    GenerationService,
    PromptPackage,
    count_generated_words,
    retryable_stream,
)
from services.generation_coverage import assess_draft_coverage
from services.model_configs import active_user_generation_route
from services.provenance import PROVENANCE_ALGORITHM, generated_paragraph_hashes
from services.usage import (
    InsufficientCreditsError,
    UsageReservation,
    record_user_key_generation,
    release_reservation,
    reserve_generation,
    settle_generation,
)
from services.writing_skills import select_writing_skills

router = APIRouter()


class ChapterGenerationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    chapter_id: str = Field(alias="chapterId", min_length=1, max_length=32)
    target_words: int = Field(alias="targetWords", ge=200, le=20000)
    model: Literal["basic", "advanced"] = "basic"
    use_style_profile: bool = Field(True, alias="useStyleProfile")
    dialogue_density: Literal["low", "mid", "high"] = Field("mid", alias="dialogueDensity")
    context_mode: Literal["smart", "fast", "standard", "deep"] = Field(
        "smart", alias="contextMode"
    )
    instruction: str = Field("", max_length=2000)


class InlineGenerationRequest(ChapterGenerationRequest):
    action: Literal["续写", "扩写", "润色", "改写语气", "按我的风格"]
    selected_text: str = Field("", alias="selectedText", max_length=20000)
    nearby_text: str = Field("", alias="nearbyText", max_length=30000)


class GenerationPreviewRequest(BaseModel):
    """Inputs accepted by the no-charge prompt preview endpoint."""

    model_config = ConfigDict(populate_by_name=True)

    chapter_id: str = Field(alias="chapterId", min_length=1, max_length=32)
    target_words: int = Field(alias="targetWords", ge=200, le=20000)
    model: Literal["basic", "advanced"] = "basic"
    use_style_profile: bool = Field(True, alias="useStyleProfile")
    dialogue_density: Literal["low", "mid", "high"] = Field("mid", alias="dialogueDensity")
    context_mode: Literal["smart", "fast", "standard", "deep"] = Field(
        "smart", alias="contextMode"
    )
    instruction: str = Field("", max_length=2000)
    action: Literal["续写", "扩写", "润色", "改写语气", "按我的风格"] | None = None
    selected_text: str = Field("", alias="selectedText", max_length=20000)
    nearby_text: str = Field("", alias="nearbyText", max_length=30000)


class DraftReviewUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    segment_ids: list[str] = Field(alias="segmentIds", min_length=1, max_length=500)
    decision: Literal["pending", "accepted", "rejected"]
    base_version: int = Field(alias="baseVersion", ge=0)


class DraftContinueRequest(BaseModel):
    """Options for continuing a failed candidate from its last checkpoint."""

    model_config = ConfigDict(populate_by_name=True)

    target_words: int = Field(alias="targetWords", ge=200, le=20000)
    model: Literal["basic", "advanced"] = "basic"
    use_style_profile: bool = Field(True, alias="useStyleProfile")
    dialogue_density: Literal["low", "mid", "high"] = Field("mid", alias="dialogueDensity")
    context_mode: Literal["smart", "fast", "standard", "deep"] = Field(
        "smart", alias="contextMode"
    )
    instruction: str = Field("", max_length=2000)


def get_generation_gateway() -> GenerationGateway:
    return GenerationGateway()


def _sse(payload: object) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _safe_generation_error(package: PromptPackage, error: Exception) -> str:
    message = str(error)
    if package.route.api_key:
        message = message.replace(package.route.api_key, "[redacted]")
    return message[:800]


async def _load_scope(
    chapter_id: str,
    user: User,
    db: AsyncSession,
    permission: ProjectPermission = ProjectPermission.VIEW,
) -> tuple[Chapter, Project]:
    result = await db.execute(
        select(Chapter, Project)
        .join(Project, Project.id == Chapter.project_id)
        .where(Chapter.id == chapter_id, Chapter.deleted_at.is_(None))
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Chapter not found")
    chapter, project = row
    await verify_project_permission(project.id, permission, user, db)
    if permission in {ProjectPermission.GENERATE, ProjectPermission.EDIT_BODY}:
        await verify_chapter_assignment(chapter, user, db)
    return chapter, project


async def _prepare(
    request: ChapterGenerationRequest,
    user: User,
    db: AsyncSession,
) -> PromptPackage:
    chapter, project = await _load_scope(request.chapter_id, user, db, ProjectPermission.GENERATE)
    service = GenerationService(db)
    user_route = await active_user_generation_route(db, user_id=user.id)
    kwargs = request.model_dump(by_alias=False)
    kwargs.pop("chapter_id")
    if isinstance(request, InlineGenerationRequest):
        kwargs.update(
            action=request.action,
            selected_text=request.selected_text,
            nearby_text=request.nearby_text,
        )
    route = None
    if user_route is not None:
        route = GenerationRoute(
            source="user",
            endpoint=user_route.endpoint,
            api_key=user_route.api_key,
            model_id=user_route.model,
            model_tier="custom",
            config_id=user_route.config_id,
            context_window_tokens=user_route.context_window_tokens,
            max_output_tokens=user_route.max_output_tokens,
            context_safety_margin_tokens=user_route.context_safety_margin_tokens,
        )
    return await service.prepare(chapter=chapter, project=project, route=route, **kwargs)


async def _prepare_preview(
    request: GenerationPreviewRequest,
    user: User,
    db: AsyncSession,
) -> PromptPackage:
    """Build the exact generation package without reserving credits or calling a model."""
    common = request.model_dump(by_alias=False, exclude_none=True)
    if request.action is not None:
        preview_request = InlineGenerationRequest(**common)
    else:
        common.pop("selected_text", None)
        common.pop("nearby_text", None)
        preview_request = ChapterGenerationRequest(**common)
    return await _prepare(preview_request, user, db)


def _enforce_generation_preflight(package: PromptPackage) -> None:
    """Reject requests that cannot fit the hard context budget."""
    preflight = package.preflight if isinstance(package.preflight, dict) else {}
    if preflight.get("blocking"):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "GENERATION_PREFLIGHT_BLOCKED",
                "message": "当前上下文超过生成预算，请先压缩设定或更新索引。",
                "preflight": preflight,
            },
        )


def _preview_payload(package: PromptPackage) -> dict:
    layers = [
        package.context.layer1_resident,
        package.context.layer2_retrieved,
        package.context.layer3_summary,
        package.context.layer4_adjacent,
    ]
    return {
        "chapterId": package.chapter.id,
        "projectId": package.project.id,
        "chapterTitle": package.chapter.title,
        "task": package.task,
        "targetWords": package.target_words,
        "model": {"id": package.model_id, "tier": package.model_tier},
        "provider": {"source": package.route.source, "configId": package.route.config_id},
        "tokenBudget": {
            "total": package.context.target_tokens,
            "prompt": package.prompt_tokens,
            "context": package.context.total_tokens,
            "trimmedLayers": package.context.trimmed_layers,
            "mode": package.context.policy.mode,
            "modelWindow": package.context.policy.model_window_tokens,
            "reservedOutput": package.context.policy.reserved_output_tokens,
            "safetyMargin": package.context.policy.safety_margin_tokens,
            "usableContext": package.preflight.get("budget"),
        },
        "contextStats": {
            "mode": package.context.policy.mode,
            "budgetTokens": package.context.target_tokens,
            "usedTokens": package.context.total_tokens,
            "modelWindowTokens": package.context.policy.model_window_tokens,
            "reservedOutputTokens": package.context.policy.reserved_output_tokens,
            "safetyMarginTokens": package.context.policy.safety_margin_tokens,
        },
        "skills": [
            {
                "id": skill.id,
                "version": skill.version,
                "category": skill.category,
                "priority": skill.priority,
            }
            for skill in package.skills.skills
        ],
        "scene": package.skills.scene,
        "coverage": package.coverage,
        "preflight": package.preflight,
        "layers": [
            {
                "key": layer.key,
                "tokens": layer.tokens,
                "items": layer.items,
                "content": layer.content,
                "availableTokens": layer.available_tokens,
                "budgetTokens": layer.budget_tokens,
                "trimReason": layer.trim_reason,
            }
            for layer in layers
        ],
        "messages": package.messages,
    }


def _generation_response(
    package: PromptPackage,
    user: User,
    db: AsyncSession,
    gateway: GenerationGateway,
    reservation: UsageReservation | None,
    request_summary: dict,
    *,
    initial_content: str = "",
) -> StreamingResponse:
    draft_id = uuid.uuid4().hex

    async def event_stream() -> AsyncIterator[str]:
        usage: dict = {}
        chunks: list[str] = []
        finalized = False
        generated_length = 0
        checkpoint_length = 0
        source_body = await db.scalar(select(ChapterBody).where(ChapterBody.chapter_id == package.chapter.id))
        draft_summary = dict(request_summary)
        # ``0`` is the immutable empty-body revision. It lets continuation
        # detect a body created after the candidate started.
        draft_summary.setdefault("sourceBodyRev", source_body.rev if source_body is not None else 0)
        draft = GenerationDraft(
            id=draft_id,
            user_id=user.id,
            project_id=package.project.id,
            chapter_id=package.chapter.id,
            kind="chapter" if package.task == "chapter" else "inline",
            status="streaming",
            content_text=initial_content,
            generated_words=count_generated_words(initial_content),
            request_summary=draft_summary,
        )
        db.add(draft)
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            if reservation is not None:
                await release_reservation(db, reservation, reason="draft_persistence_error")
            await db.commit()
            finalized = True
            yield _sse(
                {
                    "type": "error",
                    "code": "draft_persistence_error",
                    "message": "候选草稿无法保存，本次生成没有开始。请稍后重试。",
                }
            )
            return

        async def persist_failed_draft(error_code: str) -> None:
            await db.rollback()
            failed_draft = await db.get(GenerationDraft, draft_id)
            if failed_draft is None:
                return
            generated_prose = "".join(chunks)
            prose = _merge_continuation_content(initial_content, generated_prose)
            if generated_prose and failed_draft.run_id is None:
                cached = usage.get("prompt_tokens_details") or {}
                run = GenerationRun(
                    id=uuid.uuid4().hex,
                    user_id=user.id,
                    project_id=package.project.id,
                    chapter_id=package.chapter.id,
                    task_type="chapter" if package.task == "chapter" else "inline",
                    model_tier=package.model_tier,
                    prompt_tokens=int(usage.get("prompt_tokens") or package.prompt_tokens),
                    cached_tokens=int(cached.get("cached_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or tokenizer.count(generated_prose)),
                    generated_words=count_generated_words(generated_prose),
                    accepted_words=0,
                    layer_report={
                        **package.layer_report,
                        "provenance": {
                            "algorithm": PROVENANCE_ALGORITHM,
                            "paragraph_hashes": generated_paragraph_hashes(prose),
                        },
                        "incomplete": True,
                    },
                )
                db.add(run)
                await db.flush()
                failed_draft.run_id = run.id
            failed_draft.status = "failed"
            failed_draft.content_text = prose
            failed_draft.generated_words = count_generated_words(prose)
            failed_draft.error_code = error_code
        yield _sse(
            {
                "type": "meta",
                "draftId": draft_id,
                "skills": package.skills.ids,
                "scene": package.skills.scene,
                "layers": package.layer_report,
                "promptTokens": package.prompt_tokens,
                "model": package.model_id,
                "provider": package.route.source,
                "coverage": package.coverage,
                "preflight": package.preflight,
                "contextStats": package.layer_report.get("context", {}),
            }
        )
        try:
            async for event in retryable_stream(gateway, package):
                if event.type == "chunk":
                    chunks.append(event.text)
                    generated_length += len(event.text)
                    yield _sse({"type": "chunk", "text": event.text})
                    if generated_length - checkpoint_length >= 1000:
                        prose = _merge_continuation_content(initial_content, "".join(chunks))
                        draft.content_text = prose
                        draft.generated_words = count_generated_words(prose)
                        await db.commit()
                        checkpoint_length = generated_length
                elif event.usage:
                    usage = event.usage

            generated_prose = "".join(chunks)
            prose = _merge_continuation_content(initial_content, generated_prose)
            completion_tokens = int(usage.get("completion_tokens") or tokenizer.count(generated_prose))
            prompt_tokens = int(usage.get("prompt_tokens") or package.prompt_tokens)
            cached = usage.get("prompt_tokens_details") or {}
            run = GenerationRun(
                id=uuid.uuid4().hex,
                user_id=user.id,
                project_id=package.project.id,
                chapter_id=package.chapter.id,
                task_type="chapter" if package.task == "chapter" else "inline",
                model_tier=package.model_tier,
                prompt_tokens=prompt_tokens,
                cached_tokens=int(cached.get("cached_tokens") or 0),
                completion_tokens=completion_tokens,
                generated_words=count_generated_words(generated_prose),
                accepted_words=0,
                layer_report={
                    **package.layer_report,
                    "provenance": {
                        "algorithm": PROVENANCE_ALGORITHM,
                        "paragraph_hashes": generated_paragraph_hashes(prose),
                    },
                },
            )
            db.add(run)
            await db.flush()
            draft.run_id = run.id
            draft.status = "ready"
            draft.content_text = prose
            draft.generated_words = count_generated_words(prose)
            if reservation is not None:
                charged = await settle_generation(
                    db,
                    reservation,
                    run_id=run.id,
                    prompt_tokens=prompt_tokens,
                    cached_tokens=run.cached_tokens,
                    completion_tokens=completion_tokens,
                )
            else:
                await record_user_key_generation(
                    db,
                    user_id=user.id,
                    project_id=package.project.id,
                    feature="generate_chapter" if package.task == "chapter" else "generate_inline",
                    model=package.model_id,
                    run_id=run.id,
                    config_id=package.route.config_id or "unknown",
                    prompt_tokens=prompt_tokens,
                    cached_tokens=run.cached_tokens,
                    completion_tokens=completion_tokens,
                )
                charged = 0
            await db.commit()
            finalized = True
            yield _sse(
                {
                    "type": "done",
                    "runId": run.id,
                    "draftId": draft.id,
                    "generatedWords": run.generated_words,
                    "usage": {
                        "promptTokens": prompt_tokens,
                        "completionTokens": completion_tokens,
                        "cachedTokens": run.cached_tokens,
                        "credits": charged,
                    },
                }
            )
            yield "data: [DONE]\n\n"
        except GenerationProviderError as exc:
            error_code = "stream_interrupted" if exc.code == "STREAM_INTERRUPTED" else "generation_provider_error"
            await persist_failed_draft(error_code)
            if reservation is not None:
                await release_reservation(
                    db,
                    reservation,
                    reason="stream_interrupted" if exc.code == "STREAM_INTERRUPTED" else "provider_error",
                )
            await db.commit()
            finalized = True
            yield _sse(
                {
                    "type": "error",
                    "code": "STREAM_INTERRUPTED" if exc.code == "STREAM_INTERRUPTED" else "generation_provider_error",
                    "message": _safe_generation_error(package, exc),
                }
            )
        except Exception:
            await persist_failed_draft("generation_internal_error")
            if reservation is not None:
                await release_reservation(db, reservation, reason="internal_error")
            await db.commit()
            finalized = True
            yield _sse(
                {
                    "type": "error",
                    "code": "generation_internal_error",
                    "message": "生成服务内部错误，请稍后重试。",
                }
            )
        finally:
            if not finalized:
                await persist_failed_draft("stream_interrupted")
                if reservation is not None:
                    await release_reservation(db, reservation, reason="stream_interrupted")
                await db.commit()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/context/{chapter_id}")
async def preview_context(
    chapter_id: str,
    context_mode: Literal["smart", "fast", "standard", "deep"] = Query(
        "smart", alias="contextMode"
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    chapter, project = await _load_scope(chapter_id, user, db)
    user_route = await active_user_generation_route(db, user_id=user.id)
    policy = ContextBudgetPolicy.create(
        mode=context_mode,
        model_window_tokens=(
            user_route.context_window_tokens
            if user_route is not None
            else settings.generation_context_window_tokens
        ),
        reserved_output_tokens=(
            user_route.max_output_tokens
            if user_route is not None
            else settings.generation_max_output_tokens
        ),
        safety_margin_tokens=(
            user_route.context_safety_margin_tokens
            if user_route is not None
            else settings.generation_context_safety_margin_tokens
        ),
    )
    context = await GenerationService(db).assembler.build(
        project.id,
        chapter.id,
        chapter.outline or [],
        policy=policy,
    )
    selection = select_writing_skills(genre=project.genre, task="chapter", outline=chapter.outline or [])
    labels = {
        "resident": "设定与本章规划 · 常驻",
        "retrieved": "本章相关设定",
        "summary": "前情摘要",
        "adjacent": "相邻原文",
    }
    layers = [
        context.layer1_resident,
        context.layer2_retrieved,
        context.layer3_summary,
        context.layer4_adjacent,
    ]
    return {
        "layers": [
            {
                "key": layer.key,
                "label": labels[layer.key],
                "detail": f"{len(layer.items)} 项",
                "tokens": layer.tokens,
                "availableTokens": layer.available_tokens,
                "budgetTokens": layer.budget_tokens,
                "trimReason": layer.trim_reason,
                "items": layer.items,
            }
            for layer in layers
        ],
        "totalTokens": context.total_tokens,
        "budgetExceeded": context.budget_exceeded,
        "trimmedLayers": context.trimmed_layers,
        "contextMode": context.policy.mode,
        "contextBudget": context.target_tokens,
        "skills": selection.ids,
        "scene": selection.scene,
    }


@router.post("/preview")
async def preview_generation_prompt(
    request: GenerationPreviewRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the exact prompt package without model calls, reservations, or usage writes."""
    package = await _prepare_preview(request, user, db)
    return _preview_payload(package)


@router.post("/chapter")
async def generate_chapter(
    request: ChapterGenerationRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    gateway: GenerationGateway = Depends(get_generation_gateway),
):
    package = await _prepare(request, user, db)
    _enforce_generation_preflight(package)
    reservation = await _reserve(package, request, user, db)
    return _generation_response(package, user, db, gateway, reservation, _request_summary(request))


@router.post("/inline")
async def generate_inline(
    request: InlineGenerationRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    gateway: GenerationGateway = Depends(get_generation_gateway),
):
    package = await _prepare(request, user, db)
    _enforce_generation_preflight(package)
    reservation = await _reserve(package, request, user, db)
    return _generation_response(package, user, db, gateway, reservation, _request_summary(request))


def _request_summary(request: ChapterGenerationRequest) -> dict:
    summary = {
        "model": request.model,
        "targetWords": request.target_words,
        "useStyleProfile": request.use_style_profile,
        "dialogueDensity": request.dialogue_density,
        "contextMode": request.context_mode,
    }
    if request.instruction.strip():
        summary["instruction"] = request.instruction.strip()
    if isinstance(request, InlineGenerationRequest):
        summary["action"] = request.action
    return summary


def _draft_segments(draft: GenerationDraft) -> tuple[list[dict], int, bool]:
    paragraphs = [
        paragraph.strip()
        for paragraph in (draft.content_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if paragraph.strip()
    ]
    summary = draft.request_summary if isinstance(draft.request_summary, dict) else {}
    raw_review = summary.get("review")
    has_review = isinstance(raw_review, dict)
    review = raw_review if has_review else {}
    raw_decisions = review.get("decisions")
    decisions = raw_decisions if isinstance(raw_decisions, dict) else {}
    version = review.get("version", 0)
    if not isinstance(version, int) or version < 0:
        version = 0
    segments = []
    for index, text in enumerate(paragraphs, start=1):
        segment_id = f"p{index}"
        decision = decisions.get(segment_id, "pending")
        if decision not in {"pending", "accepted", "rejected"}:
            decision = "pending"
        segments.append({"id": segment_id, "text": text, "decision": decision})
    return segments, version, has_review


def _review_counts(segments: list[dict]) -> dict:
    return {
        "total": len(segments),
        "pending": sum(segment["decision"] == "pending" for segment in segments),
        "accepted": sum(segment["decision"] == "accepted" for segment in segments),
        "rejected": sum(segment["decision"] == "rejected" for segment in segments),
    }


def _accepted_draft_content(draft: GenerationDraft) -> tuple[str, bool]:
    segments, _, has_review = _draft_segments(draft)
    if not has_review:
        return draft.content_text or "", False
    return "\n".join(segment["text"] for segment in segments if segment["decision"] == "accepted"), True


def _draft_payload(
    draft: GenerationDraft,
    *,
    include_content: bool = False,
    coverage: dict | None = None,
) -> dict:
    content = draft.content_text or ""
    segments, review_version, _ = _draft_segments(draft)
    public_summary = dict(draft.request_summary or {})
    public_summary.pop("review", None)
    payload = {
        "id": draft.id,
        "runId": draft.run_id,
        "projectId": draft.project_id,
        "chapterId": draft.chapter_id,
        "kind": draft.kind,
        "status": draft.status,
        "generatedWords": draft.generated_words,
        "excerpt": content[:160] + ("…" if len(content) > 160 else ""),
        "requestSummary": public_summary,
        "errorCode": draft.error_code,
        "createdAt": draft.created_at.isoformat() if draft.created_at else None,
        "updatedAt": draft.updated_at.isoformat() if draft.updated_at else None,
        "acceptedAt": draft.accepted_at.isoformat() if draft.accepted_at else None,
        "reviewVersion": review_version,
        "review": _review_counts(segments),
    }
    if include_content:
        accepted_content, has_review = _accepted_draft_content(draft)
        payload["content"] = accepted_content if draft.status == "accepted" and has_review else content
        payload["segments"] = segments
        payload["coverage"] = coverage
    return payload


async def _draft_coverage(db: AsyncSession, draft: GenerationDraft) -> dict | None:
    if not draft.run_id:
        return None
    run = await db.get(GenerationRun, draft.run_id)
    if run is None or not isinstance(run.layer_report, dict):
        return None
    raw = run.layer_report.get("coverage")
    segments, _, has_review = _draft_segments(draft)
    review_content = draft.content_text or ""
    if has_review:
        # Rejected paragraphs must no longer count as evidence. Pending text
        # remains in scope until the author decides, and a fully reviewed draft
        # therefore assesses exactly what can be inserted.
        review_content = "\n".join(
            segment["text"] for segment in segments if segment["decision"] != "rejected"
        )
    return assess_draft_coverage(raw if isinstance(raw, dict) else None, review_content)


async def _load_draft(draft_id: str, user: User, db: AsyncSession, *, lock: bool = False) -> GenerationDraft:
    statement = select(GenerationDraft).where(GenerationDraft.id == draft_id)
    if lock:
        statement = statement.with_for_update()
    draft = (await db.execute(statement)).scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    await verify_project_permission(draft.project_id, ProjectPermission.EDIT_BODY, user, db)
    chapter = await db.get(Chapter, draft.chapter_id)
    if chapter is None or chapter.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Chapter not found")
    await verify_chapter_assignment(chapter, user, db)
    return draft


def _continuation_tail(content: str, *, max_chars: int = 12000) -> str:
    """Keep complete paragraphs only so a resumed request has a stable seam."""
    paragraphs = [
        item.strip()
        for item in content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if item.strip()
    ]
    selected: list[str] = []
    size = 0
    for paragraph in reversed(paragraphs):
        if selected and size + len(paragraph) + 1 > max_chars:
            break
        selected.append(paragraph)
        size += len(paragraph) + 1
    return "\n".join(reversed(selected))


def _merge_continuation_content(prefix: str, generated: str) -> str:
    """Join a persisted candidate prefix with new output at a paragraph boundary."""
    if not prefix:
        return generated
    if not generated:
        return prefix
    return f"{prefix.rstrip()}\n{generated.lstrip()}"


@router.post("/drafts/{draft_id}/continue")
async def continue_draft(
    draft_id: str,
    request: DraftContinueRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    gateway: GenerationGateway = Depends(get_generation_gateway),
):
    """Resume a failed draft without replacing the canonical chapter body."""
    draft = await _load_draft(draft_id, user, db)
    if draft.status != "failed" or not (draft.content_text or "").strip():
        raise HTTPException(
            status_code=409,
            detail={"code": "DRAFT_NOT_CONTINUABLE", "message": "只有包含已保存内容的中断候选可以继续生成。"},
        )
    body = await db.scalar(select(ChapterBody).where(ChapterBody.chapter_id == draft.chapter_id))
    metadata = draft.request_summary if isinstance(draft.request_summary, dict) else {}
    source_body_rev = metadata.get("sourceBodyRev")
    source_changed = (
        isinstance(source_body_rev, int)
        and (
            (source_body_rev == 0 and body is not None)
            or (source_body_rev > 0 and (body is None or body.rev != source_body_rev))
        )
    )
    if source_changed:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DRAFT_SOURCE_STALE",
                "message": "正文在候选中断后已经发生变化，请重新生成或先处理当前正文版本。",
                "sourceBodyRev": source_body_rev,
                "currentBodyRev": body.rev if body is not None else 0,
            },
        )
    tail = _continuation_tail(draft.content_text)
    if not tail:
        raise HTTPException(status_code=409, detail={"code": "DRAFT_NOT_CONTINUABLE"})
    source_hash = hashlib.sha256(draft.content_text.encode("utf-8")).hexdigest()
    continuation_instruction = (
        "这是一次从中断候选继续的请求。以下内容是候选末尾已经生成的完整段落。"
        "只从最后一句之后继续，绝不要复述、改写或重新输出这些段落；保持人物、时间、事实和语气连续。"
    )
    if request.instruction.strip():
        continuation_instruction += f"\n作者补充要求：{request.instruction.strip()}"
    inline = InlineGenerationRequest(
        chapterId=draft.chapter_id,
        targetWords=request.target_words,
        model=request.model,
        useStyleProfile=request.use_style_profile,
        dialogueDensity=request.dialogue_density,
        contextMode=request.context_mode,
        action="续写",
        selectedText="",
        nearbyText=tail,
        instruction=continuation_instruction,
    )
    package = await _prepare(inline, user, db)
    _enforce_generation_preflight(package)
    reservation = await _reserve(package, inline, user, db)
    summary = _request_summary(inline)
    summary.update(
        {
            "continuationOfDraftId": draft.id,
            "continuationSourceHash": source_hash,
            "continuationTailChars": len(tail),
            "sourceBodyRev": body.rev if body is not None else 0,
        }
    )
    return _generation_response(
        package,
        user,
        db,
        gateway,
        reservation,
        summary,
        initial_content=draft.content_text,
    )


@router.get("/drafts")
async def list_drafts(
    chapter_id: str = Query(alias="chapterId", min_length=1, max_length=32),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    chapter, _ = await _load_scope(chapter_id, user, db, ProjectPermission.EDIT_BODY)
    drafts = (
        await db.execute(
            select(GenerationDraft)
            .where(
                GenerationDraft.chapter_id == chapter.id,
                GenerationDraft.status.in_(["streaming", "ready", "failed"]),
            )
            .order_by(GenerationDraft.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    return {"items": [_draft_payload(draft) for draft in drafts]}


@router.get("/drafts/{draft_id}")
async def get_draft(
    draft_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    draft = await _load_draft(draft_id, user, db)
    return _draft_payload(
        draft,
        include_content=True,
        coverage=await _draft_coverage(db, draft),
    )


@router.patch("/drafts/{draft_id}/review")
async def update_draft_review(
    draft_id: str,
    request: DraftReviewUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    draft = await _load_draft(draft_id, user, db, lock=True)
    if draft.status not in {"ready", "failed"} or not draft.content_text:
        raise HTTPException(
            status_code=409,
            detail={"code": "DRAFT_NOT_REVIEWABLE", "message": "候选仍在生成或已经处理，无法继续审阅。"},
        )
    segments, version, _ = _draft_segments(draft)
    if request.base_version != version:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DRAFT_REVIEW_CONFLICT",
                "message": "候选已在另一窗口更新，已重新加载最新决定。",
                "currentVersion": version,
            },
        )
    valid_ids = {segment["id"] for segment in segments}
    requested_ids = set(request.segment_ids)
    if len(requested_ids) != len(request.segment_ids) or not requested_ids.issubset(valid_ids):
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_DRAFT_SEGMENTS", "message": "候选段落已经变化，请刷新后重试。"},
        )

    summary = dict(draft.request_summary or {})
    raw_review = summary.get("review")
    review = dict(raw_review) if isinstance(raw_review, dict) else {}
    raw_decisions = review.get("decisions")
    decisions = dict(raw_decisions) if isinstance(raw_decisions, dict) else {}
    for segment_id in requested_ids:
        if request.decision == "pending":
            decisions.pop(segment_id, None)
        else:
            decisions[segment_id] = request.decision
    summary["review"] = {"version": version + 1, "decisions": decisions}
    draft.request_summary = summary
    await db.commit()
    await db.refresh(draft)
    return _draft_payload(
        draft,
        include_content=True,
        coverage=await _draft_coverage(db, draft),
    )


@router.post("/drafts/{draft_id}/accept")
async def accept_draft(
    draft_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    draft = await _load_draft(draft_id, user, db, lock=True)
    if draft.status == "accepted":
        return _draft_payload(
            draft,
            include_content=True,
            coverage=await _draft_coverage(db, draft),
        )
    if draft.status not in {"ready", "failed"} or not draft.content_text:
        raise HTTPException(status_code=409, detail={"code": "DRAFT_NOT_ACCEPTABLE"})
    segments, _, has_review = _draft_segments(draft)
    if has_review and any(segment["decision"] == "pending" for segment in segments):
        raise HTTPException(
            status_code=409,
            detail={"code": "DRAFT_REVIEW_INCOMPLETE", "message": "还有段落未决定，完成审阅后再放入正文。"},
        )
    if has_review and not any(segment["decision"] == "accepted" for segment in segments):
        raise HTTPException(
            status_code=409,
            detail={"code": "DRAFT_REVIEW_EMPTY", "message": "没有接受的段落，请舍弃候选或重新选择。"},
        )
    draft.status = "accepted"
    draft.accepted_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(draft)
    return _draft_payload(
        draft,
        include_content=True,
        coverage=await _draft_coverage(db, draft),
    )


@router.delete("/drafts/{draft_id}", status_code=204)
async def reject_draft(
    draft_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    draft = await _load_draft(draft_id, user, db, lock=True)
    if draft.status == "rejected":
        return Response(status_code=204)
    if draft.status == "accepted":
        raise HTTPException(status_code=409, detail={"code": "DRAFT_ALREADY_ACCEPTED"})
    if draft.status == "streaming":
        raise HTTPException(status_code=409, detail={"code": "DRAFT_STILL_STREAMING"})
    draft.status = "rejected"
    draft.rejected_at = datetime.now(UTC)
    await db.commit()
    return Response(status_code=204)


async def _reserve(
    package: PromptPackage,
    request: ChapterGenerationRequest,
    user: User,
    db: AsyncSession,
) -> UsageReservation | None:
    if package.billing_mode == "user_key":
        return None
    try:
        return await reserve_generation(
            db,
            user_id=user.id,
            project_id=package.project.id,
            feature="generate_chapter" if package.task == "chapter" else "generate_inline",
            model=package.model_id,
            model_tier=package.model_tier,
            prompt_tokens=package.prompt_tokens,
            target_words=request.target_words,
        )
    except InsufficientCreditsError as exc:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "INSUFFICIENT_CREDITS",
                "required": exc.required,
                "remaining": exc.remaining,
            },
        ) from exc
