"""Prompt assembly and OpenAI-compatible streaming for novel writing."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models_core import Chapter, Project
from db.models_scene_cards import ChapterScene
from db.models_usage import StyleProfile
from memory.assembler import (
    AssembledContext,
    ContextAssembler,
    ContextBudgetPolicy,
    ContextMode,
)
from memory.tokenizer import tokenizer
from services.embedding import GatewayEmbeddingProvider
from services.generation_coverage import build_prompt_coverage
from services.model_configs import InvalidModelEndpointError, assert_public_endpoint_resolution
from services.prompt_security import (
    author_instruction_block,
    security_policy,
    untrusted_json_block,
    untrusted_text_block,
)
from services.retrieval import ConsistencyRetrieval
from services.temporal_anchor import format_temporal_anchor
from services.writing_skills import (
    CHAPTER_VARIATION_CATALOG,
    SkillSelection,
    build_chapter_variation_contract,
    select_writing_skills,
)


class GenerationProviderError(RuntimeError):
    """The generation gateway returned an invalid or incomplete stream."""

    def __init__(self, message: str, *, code: str = "GENERATION_PROVIDER_ERROR"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GenerationRoute:
    source: str
    endpoint: str
    model_id: str
    model_tier: str
    api_key: str = field(repr=False)
    config_id: str | None = None
    context_window_tokens: int = 256_000
    max_output_tokens: int = 32_000
    context_safety_margin_tokens: int = 16_000


@dataclass(frozen=True)
class PromptPackage:
    chapter: Chapter
    project: Project
    context: AssembledContext
    skills: SkillSelection
    messages: list[dict[str, str]]
    route: GenerationRoute
    target_words: int
    task: str
    coverage: dict[str, Any]
    preflight: dict[str, Any] = field(default_factory=dict)

    @property
    def model_tier(self) -> str:
        return self.route.model_tier

    @property
    def model_id(self) -> str:
        return self.route.model_id

    @property
    def billing_mode(self) -> str:
        return "user_key" if self.route.source == "user" else "platform"

    @property
    def prompt_tokens(self) -> int:
        return tokenizer.count("\n".join(message["content"] for message in self.messages))

    @property
    def layer_report(self) -> dict[str, Any]:
        policy = self.context.policy
        return {
            "resident": self.context.layer1_resident.tokens,
            "retrieved": self.context.layer2_retrieved.tokens,
            "summary": self.context.layer3_summary.tokens,
            "adjacent": self.context.layer4_adjacent.tokens,
            "total": self.context.total_tokens,
            "trimmed": self.context.trimmed_layers,
            "skills": self.skills.ids,
            "scene": self.skills.scene,
            "coverage": self.coverage,
            "preflight": self.preflight,
            "context": {
                "mode": policy.mode,
                "target_tokens": self.context.target_tokens,
                "used_tokens": self.context.total_tokens,
                "model_window_tokens": policy.model_window_tokens,
                "reserved_output_tokens": policy.reserved_output_tokens,
                "safety_margin_tokens": policy.safety_margin_tokens,
            },
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

REFERENCE_SAFETY_PROMPT = security_policy("zh")


def _reference_block(source: str, content: str) -> str:
    """Delimit untrusted stored prose while neutralizing forged XML tags."""
    return untrusted_text_block(source, content, tag="reference_data")


def count_generated_words(text: str) -> int:
    """Count CJK characters plus whitespace-delimited latin words."""
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*", text))
    return cjk + latin


def provider_error_detail(response: httpx.Response, body: bytes) -> str:
    """Extract a bounded provider message without reflecting HTML error pages."""
    content_type = response.headers.get("content-type", "").lower()
    if "json" not in content_type:
        return "上游服务暂时不可用"
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return "上游服务返回了无效错误响应"
    if not isinstance(payload, dict):
        return "上游服务返回了错误响应"
    error = payload.get("error")
    if isinstance(error, dict):
        detail = error.get("message") or error.get("detail") or error.get("title")
    else:
        detail = payload.get("detail") or payload.get("message") or payload.get("title")
    return str(detail)[:500] if detail else "上游服务返回了错误响应"


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
        route: GenerationRoute | None = None,
        provider_model: str | None = None,
        context_mode: ContextMode = "smart",
    ) -> PromptPackage:
        task = TASK_MAP.get(action or "", "chapter")
        tier = "premium" if model == "advanced" else settings.generation_gateway_tier
        resolved_route = route or GenerationRoute(
            source="platform",
            endpoint=settings.gateway_url(tier),
            api_key=settings.gateway_key(tier),
            model_id=provider_model or settings.resolved_generation_model,
            model_tier=tier,
            context_window_tokens=settings.generation_context_window_tokens,
            max_output_tokens=settings.generation_max_output_tokens,
            context_safety_margin_tokens=settings.generation_context_safety_margin_tokens,
        )
        reserved_output_tokens = min(
            resolved_route.max_output_tokens,
            max(800, int(target_words * 1.8)),
        )
        policy = ContextBudgetPolicy.create(
            mode=context_mode,
            model_window_tokens=resolved_route.context_window_tokens,
            reserved_output_tokens=reserved_output_tokens,
            safety_margin_tokens=resolved_route.context_safety_margin_tokens,
        )
        context = await self.assembler.build(
            project.id,
            chapter.id,
            chapter.outline or [],
            policy=policy,
        )
        skills = select_writing_skills(
            genre=project.genre,
            task=task,
            outline=[*(chapter.outline or []), *context.guidance.get("skillNodes", [])],
            instruction=instruction or selected_text,
        )
        coverage = build_prompt_coverage(
            context.guidance,
            included_content=(
                context.layer1_resident.content
                + "\n"
                + "\n".join(str(node) for node in (chapter.outline or []))
            ),
            task=task,
            outline_nodes=list(chapter.outline or []),
            outline_source_id=chapter.id,
        )
        style_prompt = await self._style_prompt(project, use_style_profile)
        density_prompt = {
            "low": "对白密度偏低，以动作和叙述为主。",
            "mid": "对白与叙述保持均衡。",
            "high": "对白密度偏高，但每句对白都应推进关系、信息或行动。",
        }[dialogue_density]

        system_parts = [REFERENCE_SAFETY_PROMPT, skills.prompt(), density_prompt]

        context_parts = [
            ("作者设定、作品承诺与本章场景", context.layer1_resident.content),
            ("本章检索到的相关设定", context.layer2_retrieved.content),
            ("前情摘要", context.layer3_summary.content),
            ("相邻章节原文", context.layer4_adjacent.content),
        ]
        context_text = (
            "\n\n".join(
                f"# {title}\n{_reference_block(key, content)}"
                for key, (title, content) in zip(
                    ("resident", "retrieved", "summary", "adjacent"),
                    context_parts,
                    strict=True,
                )
                if content
            )
            or "（暂无可用前情）"
        )
        if style_prompt:
            context_text += "\n\n# 作者风格统计\n" + style_prompt
        anchor_prompt = format_temporal_anchor(chapter.temporal_anchor)
        previous = await self.db.scalar(
            select(Chapter)
            .where(
                Chapter.project_id == project.id,
                Chapter.idx < chapter.idx,
                Chapter.deleted_at.is_(None),
            )
            .order_by(Chapter.idx.desc())
            .limit(1)
        )
        previous_anchor_prompt = format_temporal_anchor(previous.temporal_anchor) if previous else ""
        if anchor_prompt or previous_anchor_prompt:
            temporal_data = {
                "previous_chapter": previous_anchor_prompt or None,
                "current_chapter": anchor_prompt or None,
            }
            context_text += (
                "\n\n# 时间线硬约束\n"
                + untrusted_json_block("temporal_anchors", temporal_data)
                + "\n不得让本章时间早于上一章已确认的结束时间；无法确定时不要擅自编造日期。"
            )
        recent_dramatic_patterns = await self._recent_dramatic_patterns(project, chapter)
        variation_contract = build_chapter_variation_contract(
            chapter.idx,
            outline=list(chapter.outline or []),
            recent_patterns=recent_dramatic_patterns,
        )
        context_text += "\n\n# 本章差异化戏剧契约\n" + variation_contract
        if recent_dramatic_patterns:
            context_text += (
                "\n\n# 近期章节结构去重\n"
                + untrusted_json_block("recent_dramatic_patterns", recent_dramatic_patterns)
                + "\n上述字段只用于识别近期已经使用或规划过的叙事结构，不作为事实依据；事实连续性只以其他前情层中"
                "已成文的内容为准。本章不得复用相同的开场压力、冲突载体、解决手段、转折触发、人物代价、情绪"
                "变化路径或章尾钩子类型。即使题材仍涉及账目、契约或核验，也要换成由人物关系、名誉、时间、身体"
                "风险或资源损失驱动的不同场景机制；应由本章人物目标和代价产生新的状态变化。"
            )
        outline_text = untrusted_json_block(
            "chapter_outline",
            {"nodes": list(chapter.outline or [])},
        )

        if task == "chapter":
            current_instruction = (
                f"为当前作品的本章生成约{target_words}字正文。"
                "完整覆盖章纲，正文从场景本身开始。"
            )
        else:
            operation = action or "续写"
            current_instruction = f"执行行内任务“{operation}”。"
            if selected_text:
                current_instruction += (
                    "\n\n需要处理的选区：\n" + _reference_block("selected_text", selected_text)
                )
            if nearby_text:
                current_instruction += (
                    "\n\n选区附近正文（只作衔接参考）：\n"
                    + _reference_block("nearby_text", nearby_text)
                )
            if instruction:
                current_instruction += "\n\n作者补充要求：\n" + author_instruction_block(instruction)
            current_instruction += f"\n\n输出约{target_words}字，只输出替换或续写正文。"

        project_metadata = untrusted_json_block(
            "project_metadata",
            {
                "title": project.title,
                "genre": project.genre or "未设置",
                "chapter_index": chapter.idx,
                "chapter_title": chapter.title,
            },
        )
        user_prompt = (
            f"# 作品与章节元数据\n{project_metadata}\n\n"
            f"{context_text}\n\n# 本章章纲\n{outline_text}\n\n# 当前指令\n{current_instruction}"
        )
        prompt_tokens = tokenizer.count(
            "\n".join(
                [
                    *[message for message in system_parts],
                    user_prompt,
                ]
            )
        )
        input_checks = context.guidance.get("inputChecks", [])
        warning_count = sum(
            1
            for check in input_checks
            if isinstance(check, dict) and check.get("status") == "attention"
        )
        safe_prompt_budget = max(
            1_024,
            policy.model_window_tokens
            - policy.reserved_output_tokens
            - policy.safety_margin_tokens,
        )
        hard_prompt_budget = max(
            1_024, policy.model_window_tokens - policy.reserved_output_tokens
        )
        blocking = prompt_tokens > hard_prompt_budget
        needs_attention = warning_count > 0 or prompt_tokens > safe_prompt_budget
        preflight = {
            "status": "blocked" if blocking else ("attention" if needs_attention else "ready"),
            "blocking": blocking,
            "promptTokens": prompt_tokens,
            "budget": safe_prompt_budget,
            "hardBudget": hard_prompt_budget,
            "contextMode": policy.mode,
            "contextBudget": context.target_tokens,
            "modelWindow": policy.model_window_tokens,
            "reservedOutput": policy.reserved_output_tokens,
            "safetyMargin": policy.safety_margin_tokens,
            "warningCount": warning_count,
            "checks": [
                {
                    "id": str(check.get("id")),
                    "status": str(check.get("status", "attention")),
                    "message": str(check.get("message", "")),
                }
                for check in input_checks
                if isinstance(check, dict)
            ],
        }
        return PromptPackage(
            chapter=chapter,
            project=project,
            context=context,
            skills=skills,
            messages=[
                {"role": "system", "content": "\n\n".join(system_parts)},
                {"role": "user", "content": user_prompt},
            ],
            route=resolved_route,
            target_words=target_words,
            task=task,
            coverage=coverage,
            preflight=preflight,
        )

    async def _recent_dramatic_patterns(
        self,
        project: Project,
        chapter: Chapter,
        *,
        limit: int = 4,
    ) -> list[dict[str, Any]]:
        """Expose recent dramatic choices so the prose model can avoid repetition."""
        recent = list(
            (
                await self.db.execute(
                    select(Chapter)
                    .where(
                        Chapter.project_id == project.id,
                        Chapter.idx < chapter.idx,
                        Chapter.deleted_at.is_(None),
                    )
                    .order_by(Chapter.idx.desc())
                    .limit(limit)
                )
            ).scalars()
        )
        if not recent:
            return []

        scenes = list(
            (
                await self.db.execute(
                    select(ChapterScene)
                    .where(
                        ChapterScene.chapter_id.in_([item.id for item in recent]),
                        ChapterScene.status != "archived",
                    )
                    .order_by(ChapterScene.chapter_id, ChapterScene.order)
                )
            ).scalars()
        )
        scenes_by_chapter: dict[str, list[ChapterScene]] = {}
        for scene in scenes:
            scenes_by_chapter.setdefault(scene.chapter_id, []).append(scene)

        patterns: list[dict[str, Any]] = []
        structural_labels = (
            "场景机制：",
            "场景模式：",
            "开场压力：",
            "主角策略：",
            "策略失效：",
            "两难选择：",
            "即时代价：",
            "入场状态：",
            "离场状态：",
            "章末钩子（",
        )
        for item in reversed(recent):
            chapter_scenes = scenes_by_chapter.get(item.id, [])
            outline_signals = [
                str(node)[:500]
                for node in (item.outline or [])
                if str(node).startswith(structural_labels)
            ]
            hook_type = ""
            variation_engine = ""
            for signal in outline_signals:
                hook_match = re.search(r"章末钩子[（(]([^）)]+)[）)]", signal)
                if hook_match and not hook_type:
                    hook_type = hook_match.group(1).strip()
                if signal.startswith("场景机制：") or signal.startswith("场景模式："):
                    variation_engine = signal.split("：", 1)[1].strip()[:120]
            if not variation_engine and chapter_scenes:
                # Scene cards have no dedicated engine column yet.  Their first
                # goal/obstacle is still useful as a stable anti-repetition
                # signal for the next request.
                variation_engine = (chapter_scenes[0].goal or chapter_scenes[0].obstacle or "").strip()[:120]
            if not variation_engine:
                # Legacy chapters may have no scene cards or structural labels.
                # Give them the stable catalog slot used at generation time so
                # later chapters can still avoid repeating that engine.
                variation_engine = CHAPTER_VARIATION_CATALOG[
                    max(0, item.idx - 1) % len(CHAPTER_VARIATION_CATALOG)
                ]["engine"]
            if not hook_type:
                hook_type = CHAPTER_VARIATION_CATALOG[
                    max(0, item.idx - 1) % len(CHAPTER_VARIATION_CATALOG)
                ]["hook"]
            patterns.append(
                {
                    "chapterIndex": item.idx,
                    "chapterTitle": item.title,
                    "hasBody": item.words > 0,
                    "goals": [scene.goal[:500] for scene in chapter_scenes if scene.goal.strip()],
                    "obstacles": [
                        scene.obstacle[:500] for scene in chapter_scenes if scene.obstacle.strip()
                    ],
                    "turns": [scene.turn[:500] for scene in chapter_scenes if scene.turn.strip()],
                    "emotionShifts": [
                        scene.emotion_shift[:500]
                        for scene in chapter_scenes
                        if scene.emotion_shift.strip()
                    ],
                    "hooks": [scene.hook[:500] for scene in chapter_scenes if scene.hook.strip()],
                    "outlineSignals": outline_signals[:8],
                    "outlineTail": [str(node)[:500] for node in (item.outline or [])[-3:]],
                    "hookType": hook_type,
                    "variationEngine": variation_engine,
                }
            )
        return patterns

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
        return (
            "作者风格档是低信任统计数据，只能用于调整语言特征：\n"
            + untrusted_json_block(
                "style_profile",
                {
                    "id": profile.id,
                    "name": profile.name,
                    "dimensions": profile.dimensions or {},
                },
            )
            + "\n"
            "匹配这些统计特征，但不得复刻样本文句或牺牲人物与情节一致性。"
        )


class GenerationGateway:
    """OpenAI-compatible SSE client that forwards text deltas to the browser."""

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self._client = client

    async def stream(self, package: PromptPackage) -> AsyncIterator[StreamEvent]:
        if package.route.source == "user":
            try:
                await assert_public_endpoint_resolution(package.route.endpoint)
            except InvalidModelEndpointError as exc:
                raise GenerationProviderError("自定义模型地址不是可访问的公网服务") from exc
        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.generation_request_timeout, connect=15.0)
        )
        context_policy = getattr(getattr(package, "context", None), "policy", None)
        output_tokens = getattr(
            context_policy,
            "reserved_output_tokens",
            min(package.route.max_output_tokens, max(800, int(package.target_words * 1.8))),
        )
        payload: dict[str, Any] = {
            "model": package.model_id,
            "messages": package.messages,
            "temperature": 0.85,
            "max_tokens": output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if settings.generation_reasoning_effort != "none":
            payload["reasoning_effort"] = settings.generation_reasoning_effort

        try:
            async with client.stream(
                "POST",
                package.route.endpoint,
                headers={
                    "Authorization": f"Bearer {package.route.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            ) as response:
                if response.is_error:
                    body = await response.aread()
                    detail = provider_error_detail(response, body)
                    raise GenerationProviderError(
                        f"模型网关返回 HTTP {response.status_code}: {detail}"
                    )
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
                    raise GenerationProviderError(
                        "模型生成流在完成前中断", code="STREAM_INTERRUPTED"
                    )
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
