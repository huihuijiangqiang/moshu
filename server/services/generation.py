"""Prompt assembly and OpenAI-compatible streaming for novel writing."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models_core import Chapter, Project
from db.models_usage import StyleProfile
from memory.assembler import AssembledContext, ContextAssembler
from memory.tokenizer import tokenizer
from services.embedding import GatewayEmbeddingProvider
from services.retrieval import ConsistencyRetrieval
from services.writing_skills import SkillSelection, select_writing_skills


class GenerationProviderError(RuntimeError):
    """The generation gateway returned an invalid or incomplete stream."""


@dataclass(frozen=True)
class PromptPackage:
    chapter: Chapter
    project: Project
    context: AssembledContext
    skills: SkillSelection
    messages: list[dict[str, str]]
    model_tier: str
    model_id: str
    target_words: int
    task: str

    @property
    def prompt_tokens(self) -> int:
        return tokenizer.count("\n".join(message["content"] for message in self.messages))

    @property
    def layer_report(self) -> dict[str, Any]:
        return {
            "resident": self.context.layer1_resident.tokens,
            "retrieved": self.context.layer2_retrieved.tokens,
            "summary": self.context.layer3_summary.tokens,
            "adjacent": self.context.layer4_adjacent.tokens,
            "total": self.context.total_tokens,
            "trimmed": self.context.trimmed_layers,
            "skills": self.skills.ids,
            "scene": self.skills.scene,
        }


@dataclass(frozen=True)
class StreamEvent:
    type: str
    text: str = ""
    usage: Optional[dict[str, Any]] = None


TASK_MAP = {
    "续写": "continue",
    "扩写": "expand",
    "润色": "polish",
    "改写语气": "rewrite_tone",
    "按我的风格": "author_style",
}


def count_generated_words(text: str) -> int:
    """Count CJK characters plus whitespace-delimited latin words."""
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*", text))
    return cjk + latin


class GenerationService:
    def __init__(self, db: AsyncSession, *, assembler: Optional[ContextAssembler] = None):
        self.db = db
        self.assembler = assembler or ContextAssembler(
            db,
            tokenizer,
            ConsistencyRetrieval(GatewayEmbeddingProvider()),
        )

    async def prepare(
        self,
        *,
        chapter: Chapter,
        project: Project,
        target_words: int,
        model: str,
        use_style_profile: bool,
        dialogue_density: str,
        action: Optional[str] = None,
        selected_text: str = "",
        nearby_text: str = "",
        instruction: str = "",
    ) -> PromptPackage:
        task = TASK_MAP.get(action or "", "chapter")
        skills = select_writing_skills(
            genre=project.genre,
            task=task,
            outline=chapter.outline or [],
            instruction=instruction or selected_text,
        )
        context = await self.assembler.build(project.id, chapter.id, chapter.outline or [])
        style_prompt = await self._style_prompt(project, use_style_profile)
        density_prompt = {
            "low": "对白密度偏低，以动作和叙述为主。",
            "mid": "对白与叙述保持均衡。",
            "high": "对白密度偏高，但每句对白都应推进关系、信息或行动。",
        }[dialogue_density]

        system_parts = [skills.prompt(), density_prompt]
        if style_prompt:
            system_parts.append(style_prompt)

        context_parts = [
            ("作者确认的常驻设定", context.layer1_resident.content),
            ("本章检索到的相关设定", context.layer2_retrieved.content),
            ("前情摘要", context.layer3_summary.content),
            ("相邻章节原文", context.layer4_adjacent.content),
        ]
        context_text = (
            "\n\n".join(f"# {title}\n{content}" for title, content in context_parts if content) or "（暂无可用前情）"
        )
        outline_text = (
            "\n".join(f"{index + 1}. {node}" for index, node in enumerate(chapter.outline or []))
            or "（本章未设置章纲；只依据当前指令推进，不得擅自改变主线。）"
        )

        if task == "chapter":
            current_instruction = (
                f"为《{project.title}》第{chapter.idx}章《{chapter.title}》生成约{target_words}字正文。"
                "完整覆盖章纲，正文从场景本身开始。"
            )
        else:
            operation = action or "续写"
            current_instruction = f"执行行内任务“{operation}”。"
            if selected_text:
                current_instruction += f"\n\n需要处理的选区：\n{selected_text}"
            if nearby_text:
                current_instruction += f"\n\n选区附近正文（只作衔接参考）：\n{nearby_text}"
            if instruction:
                current_instruction += f"\n\n作者补充要求：\n{instruction}"
            current_instruction += f"\n\n输出约{target_words}字，只输出替换或续写正文。"

        user_prompt = (
            f"作品：{project.title}\n题材：{project.genre or '未设置'}\n"
            f"章节：第{chapter.idx}章 {chapter.title}\n\n"
            f"{context_text}\n\n# 本章章纲\n{outline_text}\n\n# 当前指令\n{current_instruction}"
        )
        tier = "premium" if model == "advanced" else settings.generation_gateway_tier
        return PromptPackage(
            chapter=chapter,
            project=project,
            context=context,
            skills=skills,
            messages=[
                {"role": "system", "content": "\n\n".join(system_parts)},
                {"role": "user", "content": user_prompt},
            ],
            model_tier=tier,
            model_id=settings.resolved_generation_model,
            target_words=target_words,
            task=task,
        )

    async def _style_prompt(self, project: Project, enabled: bool) -> str:
        if not enabled or not project.style_profile_id:
            return ""
        result = await self.db.execute(
            select(StyleProfile).where(
                StyleProfile.id == project.style_profile_id,
                StyleProfile.user_id == project.owner_id,
                StyleProfile.status == "ready",
            )
        )
        profile = result.scalar_one_or_none()
        if profile is None:
            return ""
        dimensions = json.dumps(profile.dimensions or {}, ensure_ascii=False, sort_keys=True)
        return (
            f"[style:{profile.id}] 作者风格档“{profile.name}”：{dimensions}。"
            "匹配这些统计特征，但不得复刻样本文句或牺牲人物与情节一致性。"
        )


class GenerationGateway:
    """OpenAI-compatible SSE client that forwards text deltas to the browser."""

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self._client = client

    async def stream(self, package: PromptPackage) -> AsyncIterator[StreamEvent]:
        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.generation_request_timeout, connect=15.0)
        )
        payload: dict[str, Any] = {
            "model": package.model_id,
            "messages": package.messages,
            "temperature": 0.85,
            "max_tokens": min(32000, max(800, int(package.target_words * 1.8))),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if settings.generation_reasoning_effort != "none":
            payload["reasoning_effort"] = settings.generation_reasoning_effort

        try:
            async with client.stream(
                "POST",
                settings.gateway_url(package.model_tier),
                headers={
                    "Authorization": f"Bearer {settings.gateway_key(package.model_tier)}",
                    "Content-Type": "application/json",
                },
                json=payload,
            ) as response:
                response.raise_for_status()
                seen_done = False
                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue
                    data = line[5:].lstrip()
                    if data == "[DONE]":
                        seen_done = True
                        break
                    try:
                        frame = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise GenerationProviderError("生成流包含无效 JSON") from exc
                    if frame.get("error"):
                        error = frame["error"]
                        detail = error.get("message") if isinstance(error, dict) else str(error)
                        raise GenerationProviderError(f"模型网关错误：{detail}")
                    usage = frame.get("usage")
                    if isinstance(usage, dict):
                        yield StreamEvent("usage", usage=usage)
                    choices = frame.get("choices") or []
                    if choices:
                        content = (choices[0].get("delta") or {}).get("content")
                        if isinstance(content, str) and content:
                            yield StreamEvent("chunk", text=content)
                if not seen_done:
                    raise GenerationProviderError("模型生成流在完成前中断")
        except httpx.HTTPStatusError as exc:
            body = await exc.response.aread()
            detail = body.decode("utf-8", errors="replace")[:500]
            raise GenerationProviderError(f"模型网关返回 HTTP {exc.response.status_code}: {detail}") from exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise GenerationProviderError(f"模型网关连接失败：{exc}") from exc
        finally:
            if not self._client:
                await client.aclose()


async def retryable_stream(
    gateway: GenerationGateway,
    package: PromptPackage,
    *,
    retries: int = 2,
) -> AsyncIterator[StreamEvent]:
    """Retry only before the first visible delta so prose is never duplicated."""
    for attempt in range(retries + 1):
        emitted = False
        try:
            async for event in gateway.stream(package):
                if event.type == "chunk":
                    emitted = True
                yield event
            return
        except GenerationProviderError:
            if emitted or attempt >= retries:
                raise
            await asyncio.sleep(0.5 * (2**attempt))
