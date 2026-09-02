"""Authenticated chapter and inline novel generation endpoints."""

from __future__ import annotations

import json
import uuid
from typing import AsyncIterator, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import ProjectPermission, get_current_user, verify_project_permission
from db.models_core import Chapter, Project, User
from db.models_usage import GenerationRun
from db.session import get_db
from memory.tokenizer import tokenizer
from services.generation import (
    GenerationGateway,
    GenerationProviderError,
    GenerationService,
    PromptPackage,
    count_generated_words,
    retryable_stream,
)
from services.provenance import PROVENANCE_ALGORITHM, generated_paragraph_hashes
from services.usage import (
    InsufficientCreditsError,
    UsageReservation,
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
    instruction: str = Field("", max_length=2000)


class InlineGenerationRequest(ChapterGenerationRequest):
    action: Literal["续写", "扩写", "润色", "改写语气", "按我的风格"]
    selected_text: str = Field("", alias="selectedText", max_length=20000)
    nearby_text: str = Field("", alias="nearbyText", max_length=30000)


def get_generation_gateway() -> GenerationGateway:
    return GenerationGateway()


def _sse(payload: object) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _load_scope(
    chapter_id: str,
    user: User,
    db: AsyncSession,
    permission: ProjectPermission = ProjectPermission.VIEW,
) -> tuple[Chapter, Project]:
    result = await db.execute(
        select(Chapter, Project).join(Project, Project.id == Chapter.project_id).where(Chapter.id == chapter_id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Chapter not found")
    chapter, project = row
    await verify_project_permission(project.id, permission, user, db)
    return chapter, project


async def _prepare(
    request: ChapterGenerationRequest,
    user: User,
    db: AsyncSession,
) -> PromptPackage:
    chapter, project = await _load_scope(request.chapter_id, user, db, ProjectPermission.GENERATE)
    service = GenerationService(db)
    kwargs = request.model_dump(by_alias=False)
    kwargs.pop("chapter_id")
    if isinstance(request, InlineGenerationRequest):
        kwargs.update(
            action=request.action,
            selected_text=request.selected_text,
            nearby_text=request.nearby_text,
        )
    return await service.prepare(chapter=chapter, project=project, **kwargs)


def _generation_response(
    package: PromptPackage,
    user: User,
    db: AsyncSession,
    gateway: GenerationGateway,
    reservation: UsageReservation,
) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[str]:
        usage: dict = {}
        chunks: list[str] = []
        finalized = False
        yield _sse(
            {
                "type": "meta",
                "skills": package.skills.ids,
                "scene": package.skills.scene,
                "layers": package.layer_report,
                "promptTokens": package.prompt_tokens,
                "model": package.model_id,
            }
        )
        try:
            async for event in retryable_stream(gateway, package):
                if event.type == "chunk":
                    chunks.append(event.text)
                    yield _sse({"type": "chunk", "text": event.text})
                elif event.usage:
                    usage = event.usage

            prose = "".join(chunks)
            completion_tokens = int(usage.get("completion_tokens") or tokenizer.count(prose))
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
                generated_words=count_generated_words(prose),
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
            charged = await settle_generation(
                db,
                reservation,
                run_id=run.id,
                prompt_tokens=prompt_tokens,
                cached_tokens=run.cached_tokens,
                completion_tokens=completion_tokens,
            )
            await db.commit()
            finalized = True
            yield _sse(
                {
                    "type": "done",
                    "runId": run.id,
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
            await db.rollback()
            await release_reservation(db, reservation, reason="provider_error")
            await db.commit()
            finalized = True
            yield _sse({"type": "error", "code": "generation_provider_error", "message": str(exc)})
        except Exception:
            await db.rollback()
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
                await db.rollback()
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
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    chapter, project = await _load_scope(chapter_id, user, db)
    context = await GenerationService(db).assembler.build(project.id, chapter.id, chapter.outline or [])
    selection = select_writing_skills(genre=project.genre, task="chapter", outline=chapter.outline or [])
    labels = {
        "resident": "稳定设定 · 常驻",
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
                "items": layer.items,
            }
            for layer in layers
        ],
        "totalTokens": context.total_tokens,
        "budgetExceeded": context.budget_exceeded,
        "trimmedLayers": context.trimmed_layers,
        "skills": selection.ids,
        "scene": selection.scene,
    }


@router.post("/chapter")
async def generate_chapter(
    request: ChapterGenerationRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    gateway: GenerationGateway = Depends(get_generation_gateway),
):
    package = await _prepare(request, user, db)
    reservation = await _reserve(package, request, user, db)
    return _generation_response(package, user, db, gateway, reservation)


@router.post("/inline")
async def generate_inline(
    request: InlineGenerationRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    gateway: GenerationGateway = Depends(get_generation_gateway),
):
    package = await _prepare(request, user, db)
    reservation = await _reserve(package, request, user, db)
    return _generation_response(package, user, db, gateway, reservation)


async def _reserve(
    package: PromptPackage,
    request: ChapterGenerationRequest,
    user: User,
    db: AsyncSession,
) -> UsageReservation:
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
