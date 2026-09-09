"""
四层上下文装配器 - 整个系统的核心
装配顺序和字节稳定性直接影响 prompt cache 命中率
"""

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Literal, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from db.models_codex import CodexEntry, CodexRelation
from db.models_consistency import ChapterOutlineState
from db.models_consistency_extended import ConsistencyClaim, DocumentSummary
from db.models_core import Chapter, ChapterBody, Volume
from db.models_positioning import ProjectPositioning
from db.models_scene_cards import ChapterScene
from services.codex_states import latest_author_states
from services.retrieval import ConsistencyRetrieval
from services.timeline import author_override_offset


class _PlainTextParser(HTMLParser):
    """Small dependency-free HTML to text converter for stored TipTap prose."""

    BLOCK_TAGS = frozenset({"p", "div", "br", "li", "h1", "h2", "h3", "blockquote"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.BLOCK_TAGS and self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BLOCK_TAGS and self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return "\n".join(line.strip() for line in "".join(self.parts).splitlines() if line.strip())


def html_to_text(content_html: str) -> str:
    parser = _PlainTextParser()
    parser.feed(content_html or "")
    return parser.text()


def _format_temporal_offset(seconds: float) -> str:
    if seconds == 0:
        return "同一时刻"
    direction = "之后" if seconds > 0 else "之前"
    remaining = abs(seconds)
    days = int(remaining // 86400)
    hours = int(round((remaining % 86400) / 3600))
    if hours == 24:
        days += 1
        hours = 0
    value = "".join((f"{days}天" if days else "", f"{hours}小时" if hours else ""))
    return f"{direction}{value or '不足1小时'}"


@dataclass
class ContextLayer:
    """单层上下文"""

    key: str  # resident, retrieved, summary, adjacent
    content: str
    tokens: int
    items: list[dict]  # 用于右栏展示
    available_tokens: int = 0
    budget_tokens: int = 0
    trim_reason: str | None = None


ContextMode = Literal["smart", "fast", "standard", "deep"]


@dataclass(frozen=True)
class ContextBudgetPolicy:
    """Model-aware input budget with an explicit output and transport reserve."""

    mode: ContextMode = "smart"
    model_window_tokens: int = 256_000
    reserved_output_tokens: int = 32_000
    safety_margin_tokens: int = 16_000
    max_context_tokens: int = 208_000

    @classmethod
    def create(
        cls,
        *,
        mode: ContextMode = "smart",
        model_window_tokens: int = 256_000,
        reserved_output_tokens: int = 32_000,
        safety_margin_tokens: int = 16_000,
    ) -> "ContextBudgetPolicy":
        usable = max(1_024, model_window_tokens - reserved_output_tokens - safety_margin_tokens)
        # 208K is the tested material ceiling. Larger provider windows do not
        # silently increase cost and latency until they have their own evals.
        return cls(
            mode=mode,
            model_window_tokens=model_window_tokens,
            reserved_output_tokens=reserved_output_tokens,
            safety_margin_tokens=safety_margin_tokens,
            max_context_tokens=min(208_000, usable),
        )

    def target_for(self, available_tokens: int) -> int:
        limits = {"fast": 64_000, "standard": 128_000, "deep": self.max_context_tokens}
        if self.mode in limits:
            return min(self.max_context_tokens, limits[self.mode])
        if available_tokens <= 64_000:
            return min(self.max_context_tokens, 64_000)
        if available_tokens <= 128_000:
            return min(self.max_context_tokens, 128_000)
        return self.max_context_tokens

    def layer_budgets(self, total: int) -> dict[str, int]:
        # Deep-mode ceilings: 40K authoritative state, 40K related Codex,
        # 56K summaries/distant evidence and 72K recent prose.
        weights = {"resident": 40, "retrieved": 40, "summary": 56, "adjacent": 72}
        result = {key: total * weight // 208 for key, weight in weights.items()}
        result["adjacent"] += total - sum(result.values())
        return result

    @property
    def adjacent_chapters(self) -> int:
        return {"fast": 2, "standard": 6, "deep": 12, "smart": 12}[self.mode]


@dataclass
class AssembledContext:
    """装配完成的四层上下文"""

    layer1_resident: ContextLayer
    layer2_retrieved: ContextLayer
    layer3_summary: ContextLayer
    layer4_adjacent: ContextLayer
    total_tokens: int
    budget_exceeded: bool
    trimmed_layers: list[str]  # 被削减的层
    guidance: dict = field(default_factory=dict)
    policy: ContextBudgetPolicy = field(default_factory=ContextBudgetPolicy.create)
    target_tokens: int = 0


class ContextAssembler:
    """上下文装配器 - 四层预算分配与裁剪"""

    # Backward-compatible public ceilings. Each request now uses a policy
    # derived from the selected model instead of treating these as hard-coded.
    BUDGET_TOTAL = 208_000
    BUDGET_LAYER1 = 40_000
    BUDGET_LAYER2 = 40_000
    BUDGET_LAYER3 = 56_000
    BUDGET_LAYER4 = 72_000

    def __init__(
        self,
        db: AsyncSession,
        tokenizer,
        retrieval: Optional[ConsistencyRetrieval] = None,
    ):
        self.db = db
        self.tokenizer = tokenizer
        self.retrieval = retrieval

    async def build(
        self,
        project_id: str,
        chapter_id: str,
        outline_nodes: list[str],
        *,
        policy: ContextBudgetPolicy | None = None,
    ) -> AssembledContext:
        """
        装配四层上下文

        Args:
            project_id: 项目ID
            chapter_id: 当前章节ID（用于获取前文）
            outline_nodes: 章纲节点，用于实体抽取和检索

        Returns:
            装配完成的四层上下文
        """
        policy = policy or ContextBudgetPolicy.create()

        # Layer 1: 常驻设定 + 作者明确写下的作品承诺和本章场景计划。
        # Guidance is read once here and reused by retrieval, prompt preview and
        # real generation, so those paths cannot silently diverge.
        story_guidance, guidance = await self._build_story_guidance(project_id, chapter_id)
        story_guidance_budget = min(8_000, max(2_600, policy.max_context_tokens // 16))
        trimmed_story_guidance = self._trim_layer(story_guidance, story_guidance_budget)
        guidance["storyGuidanceTrimmed"] = trimmed_story_guidance.tokens < story_guidance.tokens
        layer1 = await self._build_layer1_resident(
            project_id,
            chapter_id,
            story_guidance=trimmed_story_guidance,
        )

        # Scene/positioning text participates in the existing exact/vector
        # retrieval route instead of creating a second RAG implementation.
        retrieval_nodes = [*outline_nodes, *guidance.pop("retrievalNodes", [])]
        structured_results = await self._retrieve_structured_evidence(
            project_id,
            chapter_id,
            retrieval_nodes,
            near_window=policy.adjacent_chapters,
            top_k={"fast": 8, "standard": 16, "deep": 24, "smart": 24}[policy.mode],
        )
        layer2 = await self._build_layer2_retrieved(
            project_id,
            chapter_id,
            retrieval_nodes,
            structured_results=structured_results,
        )

        # Layer 3: 前情摘要（本卷章摘要 + 更早的卷摘要）
        layer3 = await self._build_layer3_summary(
            project_id,
            chapter_id,
            retrieval_nodes,
            structured_results=structured_results,
        )

        # Layer 4: 相邻原文（数量随模式扩展，从章末往前截取）
        layer4 = await self._build_layer4_adjacent(
            chapter_id, limit=policy.adjacent_chapters
        )

        raw_layers = {
            "resident": layer1,
            "retrieved": layer2,
            "summary": layer3,
            "adjacent": layer4,
        }
        available_total = sum(layer.tokens for layer in raw_layers.values())
        target = policy.target_for(available_total)
        budgets = policy.layer_budgets(target)
        packed = {
            key: self._trim_layer(layer, budgets[key], keep_tail=key == "adjacent")
            for key, layer in raw_layers.items()
        }

        # Sparse layers lend unused space to the most continuity-sensitive
        # material. Long works grow naturally without padding short prompts.
        remaining = max(0, target - sum(layer.tokens for layer in packed.values()))
        for key in ("adjacent", "resident", "retrieved", "summary"):
            if remaining <= 0:
                break
            raw = raw_layers[key]
            current = packed[key]
            requested = min(raw.tokens, current.tokens + remaining)
            expanded = self._trim_layer(raw, requested, keep_tail=key == "adjacent")
            remaining -= max(0, expanded.tokens - current.tokens)
            packed[key] = expanded

        trimmed = ["story_guidance"] if guidance["storyGuidanceTrimmed"] else []
        for key, raw in raw_layers.items():
            selected = packed[key]
            reason = None
            if selected.tokens < raw.tokens:
                reason = "budget_limit"
                trimmed.append(key)
            selected.available_tokens = raw.tokens
            selected.budget_tokens = max(budgets[key], selected.tokens)
            selected.trim_reason = reason

        layer1 = packed["resident"]
        layer2 = packed["retrieved"]
        layer3 = packed["summary"]
        layer4 = packed["adjacent"]
        total = sum(layer.tokens for layer in packed.values())

        return AssembledContext(
            layer1_resident=layer1,
            layer2_retrieved=layer2,
            layer3_summary=layer3,
            layer4_adjacent=layer4,
            total_tokens=total,
            budget_exceeded=len(trimmed) > 0,
            trimmed_layers=trimmed,
            guidance=guidance,
            policy=policy,
            target_tokens=target,
        )

    async def _build_story_guidance(
        self,
        project_id: str,
        chapter_id: str,
    ) -> tuple[ContextLayer, dict]:
        """Load author-owned positioning and scene plans as generation evidence."""
        positioning = await self.db.scalar(
            select(ProjectPositioning).where(
                ProjectPositioning.project_id == project_id,
                ProjectPositioning.status != "archived",
            )
        )
        scenes = list(
            (
                await self.db.execute(
                    select(ChapterScene)
                    .where(
                        ChapterScene.chapter_id == chapter_id,
                        ChapterScene.status != "archived",
                    )
                    .order_by(ChapterScene.order, ChapterScene.id)
                )
            ).scalars()
        )
        ref_ids = {
            entry_id
            for scene in scenes
            for entry_id in (scene.pov_entry_id, scene.location_entry_id)
            if entry_id
        }
        ref_entries = {}
        if ref_ids:
            ref_entries = {
                entry.id: entry
                for entry in (
                    await self.db.execute(
                        select(CodexEntry).where(
                            CodexEntry.id.in_(ref_ids),
                            CodexEntry.project_id == project_id,
                            CodexEntry.status == "confirmed",
                        )
                    )
                ).scalars()
            }
        outline_state = await self.db.scalar(
            select(ChapterOutlineState).where(ChapterOutlineState.chapter_id == chapter_id)
        )
        chapter_body = await self.db.scalar(
            select(ChapterBody).where(ChapterBody.chapter_id == chapter_id)
        )
        current_outline_rev = outline_state.revision if outline_state else 0
        current_body_rev = chapter_body.rev if chapter_body else None

        lines = [
            "以下内容来自作者的作品定位和场景卡。生成整章时按场景顺序自然推进；"
            "行内任务只把它们作为一致性参考。长线承诺需要持续推进，不要求在本章一次完结。"
        ]
        items: list[dict] = []
        requirements: list[dict] = []
        input_checks: list[dict] = []
        retrieval_nodes: list[str] = []

        if positioning is None:
            input_checks.append(
                {
                    "id": "positioning.missing",
                    "checkType": "input",
                    "sourceType": "positioning",
                    "sourceId": None,
                    "label": "作品承诺",
                    "status": "attention",
                    "severity": "warning",
                    "message": "尚未建立可用的作品定位，本次只能依据题材、章纲和设定生成。",
                    "expected": [],
                    "evidence": [],
                }
            )
        else:
            platform_labels = {
                "fanqie": "番茄",
                "qimao": "七猫",
                "qidian": "起点",
                "general": "通用",
            }
            lines.extend(
                [
                    "# 作品定位与读者承诺",
                    f"- 目标平台：{platform_labels.get(positioning.platform, positioning.platform)}",
                    f"- 定位状态：{'已启用' if positioning.status == 'active' else '草稿（仍按作者当前版本执行）'}",
                ]
            )
            positioning_fields = (
                ("selling_point", "核心卖点", positioning.selling_point),
                ("synopsis", "对外简介", positioning.synopsis),
                ("protagonist_dilemma", "主角困境", positioning.protagonist_dilemma),
                ("first_payoff", "首个兑现点", positioning.first_payoff),
                ("long_term_arc", "长线承诺", positioning.long_term_arc),
            )
            for field_name, label, value in positioning_fields:
                value = (value or "").strip()
                if not value:
                    continue
                lines.append(f"- {label}：{value}")
                retrieval_nodes.append(value[:800])
                requirements.append(
                    {
                        "id": f"positioning.{field_name}",
                        "sourceType": "positioning",
                        "sourceId": positioning.id,
                        "label": label,
                        "expected": [value],
                    }
                )
            if positioning.tags:
                lines.append("- 题材标签：" + "、".join(str(tag) for tag in positioning.tags if str(tag).strip()))
            items.append(
                {
                    "id": positioning.id,
                    "name": "作品定位",
                    "kind": "positioning",
                    "revision": positioning.revision,
                    "status": positioning.status,
                }
            )
            missing = [
                label
                for _, label, value in positioning_fields
                if label != "对外简介" and not (value or "").strip()
            ]
            if missing:
                input_checks.append(
                    {
                        "id": "positioning.incomplete",
                        "checkType": "input",
                        "sourceType": "positioning",
                        "sourceId": positioning.id,
                        "label": "作品承诺完整度",
                        "status": "attention",
                        "severity": "warning",
                        "message": "仍缺少：" + "、".join(missing) + "。",
                        "expected": missing,
                        "evidence": [],
                    }
                )
            if positioning.status == "draft":
                input_checks.append(
                    {
                        "id": "positioning.draft",
                        "checkType": "input",
                        "sourceType": "positioning",
                        "sourceId": positioning.id,
                        "label": "定位版本状态",
                        "status": "attention",
                        "severity": "info",
                        "message": "当前定位仍为草稿；本次会使用该版本，并在预览中明确展示。",
                        "expected": [],
                        "evidence": [],
                    }
                )

        if not scenes:
            input_checks.append(
                {
                    "id": "scenes.missing",
                    "checkType": "input",
                    "sourceType": "scene",
                    "sourceId": None,
                    "label": "本章场景计划",
                    "status": "attention",
                    "severity": "warning",
                    "message": "本章没有场景卡，本次会退回到章纲节点推进。",
                    "expected": [],
                    "evidence": [],
                }
            )
        else:
            lines.append("# 本章场景计划")
            scene_fields = (
                ("goal", "目标"),
                ("obstacle", "阻力"),
                ("turn", "转折"),
                ("info_gain", "信息增量"),
                ("emotion_shift", "情绪变化"),
                ("hook", "离场钩子"),
            )
            for scene in scenes:
                pov = ref_entries.get(scene.pov_entry_id)
                location = ref_entries.get(scene.location_entry_id)
                lines.append(f"## 场景 {scene.order}（{scene.status}）")
                if pov:
                    lines.append(f"- 叙事视角：{pov.name}")
                    retrieval_nodes.append(pov.name)
                    requirements.append(
                        {
                            "id": f"scene.{scene.id}.pov",
                            "sourceType": "scene",
                            "sourceId": scene.id,
                            "label": f"场景 {scene.order} · 叙事视角",
                            "expected": [pov.name],
                        }
                    )
                if location:
                    lines.append(f"- 地点：{location.name}")
                    retrieval_nodes.append(location.name)
                    requirements.append(
                        {
                            "id": f"scene.{scene.id}.location",
                            "sourceType": "scene",
                            "sourceId": scene.id,
                            "label": f"场景 {scene.order} · 地点",
                            "expected": [location.name],
                        }
                    )
                for attr, label in scene_fields:
                    value = (getattr(scene, attr) or "").strip()
                    if value:
                        lines.append(f"- {label}：{value}")
                        retrieval_nodes.append(value[:800])
                        requirements.append(
                            {
                                "id": f"scene.{scene.id}.{attr}",
                                "sourceType": "scene",
                                "sourceId": scene.id,
                                "label": f"场景 {scene.order} · {label}",
                                "expected": [value],
                            }
                        )
                items.append(
                    {
                        "id": scene.id,
                        "name": f"场景 {scene.order}",
                        "kind": "scene",
                        "order": scene.order,
                        "status": scene.status,
                    }
                )
                missing = [
                    label
                    for attr, label in scene_fields[:3]
                    if not (getattr(scene, attr) or "").strip()
                ]
                if missing:
                    input_checks.append(
                        {
                            "id": f"scene.{scene.id}.incomplete",
                            "checkType": "input",
                            "sourceType": "scene",
                            "sourceId": scene.id,
                            "label": f"场景 {scene.order} 完整度",
                            "status": "attention",
                            "severity": "warning",
                            "message": "仍缺少：" + "、".join(missing) + "。",
                            "expected": missing,
                            "evidence": [],
                        }
                    )
                if scene.outline_rev != current_outline_rev:
                    input_checks.append(
                        {
                            "id": f"scene.{scene.id}.stale",
                            "checkType": "input",
                            "sourceType": "scene",
                            "sourceId": scene.id,
                            "label": f"场景 {scene.order} 与章纲版本",
                            "status": "attention",
                            "severity": "warning",
                            "message": (
                                f"场景基于章纲第 {scene.outline_rev} 版，当前为第 {current_outline_rev} 版；"
                                "生成前建议复核。"
                            ),
                            "expected": [str(current_outline_rev)],
                            "evidence": [str(scene.outline_rev)],
                        }
                    )
                if scene.body_rev != current_body_rev:
                    input_checks.append(
                        {
                            "id": f"scene.{scene.id}.body_stale",
                            "checkType": "input",
                            "sourceType": "scene",
                            "sourceId": scene.id,
                            "label": f"场景 {scene.order} 与正文版本",
                            "status": "attention",
                            "severity": "warning",
                            "message": (
                                f"场景基于正文第 {scene.body_rev or 0} 版，当前为第 {current_body_rev or 0} 版；"
                                "若保留现有正文，请先复核衔接。"
                            ),
                            "expected": [str(current_body_rev or 0)],
                            "evidence": [str(scene.body_rev or 0)],
                        }
                    )

        content = "\n".join(lines) if len(lines) > 1 else ""
        # Keep the embedding/exact-retrieval query bounded independently from
        # the elastic prompt budget.
        bounded_nodes: list[str] = []
        remaining = 6000
        for node in retrieval_nodes:
            if remaining <= 0:
                break
            value = node[:remaining]
            if value:
                bounded_nodes.append(value)
                remaining -= len(value)
        max_report_checks = 200
        if len(requirements) > max_report_checks:
            omitted = len(requirements) - max_report_checks
            requirements = requirements[:max_report_checks]
            input_checks.append(
                {
                    "id": "coverage.requirements_limited",
                    "checkType": "input",
                    "sourceType": "plan",
                    "sourceId": None,
                    "label": "覆盖报告范围",
                    "status": "attention",
                    "severity": "warning",
                    "message": f"规划项过多，报告只逐项追踪前 {max_report_checks} 项，另有 {omitted} 项请人工复核。",
                    "expected": [],
                    "evidence": [],
                }
            )
        if len(input_checks) > max_report_checks:
            omitted = len(input_checks) - max_report_checks
            input_checks = input_checks[: max_report_checks - 1] + [
                {
                    "id": "coverage.input_checks_limited",
                    "checkType": "input",
                    "sourceType": "plan",
                    "sourceId": None,
                    "label": "规划风险范围",
                    "status": "attention",
                    "severity": "warning",
                    "message": f"风险项过多，另有 {omitted + 1} 项未逐项展示，请先精简或整理场景卡。",
                    "expected": [],
                    "evidence": [],
                }
            ]
        return (
            ContextLayer("story_guidance", content, self.tokenizer.count(content), items),
            {
                "inputChecks": input_checks,
                "requirements": requirements,
                "retrievalNodes": bounded_nodes,
                "skillNodes": bounded_nodes,
            },
        )

    async def _outgoing_relation_lines(
        self, project_id: str, entry_ids: list[str]
    ) -> dict[str, list[str]]:
        if not entry_ids:
            return {}
        target = aliased(CodexEntry)
        rows = (
            await self.db.execute(
                select(CodexRelation, target.name, target.kind)
                .join(target, target.id == CodexRelation.to_id)
                .where(
                    CodexRelation.from_id.in_(entry_ids),
                    target.project_id == project_id,
                    target.status == "confirmed",
                )
                .order_by(CodexRelation.from_id, CodexRelation.relation_type, target.name)
            )
        ).all()
        by_entry: dict[str, list[str]] = {}
        for relation, target_name, target_kind in rows:
            detail = f" - {relation.description.strip()}" if relation.description else ""
            by_entry.setdefault(relation.from_id, []).append(
                f"- {relation.relation_type} -> {target_name} ({target_kind}){detail}"
            )
        return by_entry

    async def _build_layer1_resident(
        self,
        project_id: str,
        chapter_id: str,
        *,
        story_guidance: ContextLayer,
    ) -> ContextLayer:
        """
        Layer 1: 常驻设定
        - 全量加载 resident=True 的条目
        - 按 id 排序（而非 updated_at）确保字节稳定，命中 prompt cache
        """
        stmt = (
            select(CodexEntry)
            .where(
                CodexEntry.project_id == project_id,
                CodexEntry.resident,
                CodexEntry.status == "confirmed",
            )
            .order_by(CodexEntry.id)  # 关键：按 id 排序，不按时间
        )
        result = await self.db.execute(stmt)
        entries = result.scalars().all()

        relations_by_entry = await self._outgoing_relation_lines(
            project_id, [entry.id for entry in entries]
        )

        # 序列化为文本
        lines = []
        items = []
        if story_guidance.content:
            lines.append(f"# 作者规划的作品承诺与本章场景\n{story_guidance.content}")
            items.extend(story_guidance.items)
        for entry in entries:
            text = f"## {entry.name} ({entry.kind})\n{entry.description}\n"
            if entry.attrs:
                text += f"属性: {json.dumps(entry.attrs, ensure_ascii=False, sort_keys=True)}\n"
            if relations_by_entry.get(entry.id):
                text += "关系:\n" + "\n".join(relations_by_entry[entry.id]) + "\n"
            lines.append(text)
            items.append({"id": entry.id, "name": entry.name, "kind": entry.kind})

        author_states = await self._build_author_state_facts(
            project_id,
            chapter_id,
            [entry.id for entry in entries],
        )
        if author_states.content:
            lines.append(f"# 作者记录的当前状态\n{author_states.content}")
            items.extend(author_states.items)

        timeline = await self._build_confirmed_temporal_facts(project_id)
        if timeline.content:
            lines.append(f"# 作者确认的时间事实\n{timeline.content}")
            items.extend(timeline.items)

        content = "\n".join(lines)
        tokens = self.tokenizer.count(content)

        return ContextLayer(key="resident", content=content, tokens=tokens, items=items)

    async def _build_author_state_facts(
        self,
        project_id: str,
        chapter_id: str,
        entry_ids: list[str],
    ) -> ContextLayer:
        states = await latest_author_states(
            self.db,
            project_id=project_id,
            chapter_id=chapter_id,
            entry_ids=entry_ids,
        )
        lines: list[str] = []
        items: list[dict] = []
        for entry_id in sorted(states):
            for state in states[entry_id]:
                lines.append(
                    f"- {entry_id} / {state['state_key']}：{state['value']}"
                    f"（截至第{state['chapter_index']}章）"
                )
                items.append(
                    {
                        "id": f"state:{entry_id}:{state['state_key']}",
                        "name": str(state["state_key"]),
                        "kind": "state",
                    }
                )
        content = "\n".join(lines)
        return ContextLayer("author_states", content, self.tokenizer.count(content), items)

    async def _build_confirmed_temporal_facts(self, project_id: str) -> ContextLayer:
        """Expose author-confirmed fuzzy-time decisions to generation, not only rules."""
        result = await self.db.execute(
            select(ConsistencyClaim, Chapter)
            .outerjoin(Chapter, Chapter.id == ConsistencyClaim.chapter_id)
            .where(
                ConsistencyClaim.project_id == project_id,
                ConsistencyClaim.status == "accepted",
                ConsistencyClaim.order_basis == "relative_to_anchor",
            )
            .order_by(ConsistencyClaim.id)
        )
        lines: list[str] = []
        items: list[dict] = []
        for claim, chapter in result.all():
            offset = author_override_offset(
                {
                    "temporal_resolution": claim.temporal_resolution,
                    "temporal_anchor_text": claim.temporal_anchor_text,
                    "temporal_relation": claim.temporal_relation,
                }
            )
            if offset is None:
                continue
            event = claim.temporal_event_ref or claim.subject_text
            reference = claim.temporal_relation_ref or "参照事件"
            chapter_label = f"第{chapter.idx}章" if chapter else "全书设定"
            lines.append(
                f"- {event}：原文“{claim.temporal_anchor_text}”，作者确认为相对“{reference}”"
                f"{_format_temporal_offset(offset)}（{chapter_label}）。"
            )
            items.append({"id": f"timeline:{claim.id}", "name": event, "kind": "timeline"})
        content = "\n".join(lines)
        return ContextLayer("confirmed_timeline", content, self.tokenizer.count(content), items)

    async def _build_layer2_retrieved(
        self,
        project_id: str,
        chapter_id: str,
        outline_nodes: list[str],
        *,
        structured_results: list[dict] | None = None,
    ) -> ContextLayer:
        """
        Layer 2: 章纲精确命中设定名/别名，向量检索补足语义相关条目。
        """
        query_text = "\n".join(node.strip() for node in outline_nodes if node.strip())
        if not query_text:
            return ContextLayer(key="retrieved", content="", tokens=0, items=[])

        result = await self.db.execute(
            select(CodexEntry)
            .options(selectinload(CodexEntry.aliases))
            .where(
                CodexEntry.project_id == project_id,
                CodexEntry.status == "confirmed",
                ~CodexEntry.resident,
            )
            .order_by(CodexEntry.id)
        )
        entries = list(result.scalars().unique().all())
        entries_by_id = {entry.id: entry for entry in entries}
        matched = {
            entry.id: entry
            for entry in entries
            if entry.name in query_text or any(alias.alias and alias.alias in query_text for alias in entry.aliases)
        }

        bind = self.db.get_bind()
        supports_vector = bind is not None and bind.dialect.name == "postgresql"
        retrieval_metadata: dict[str, dict] = {}
        if structured_results is not None:
            semantic_ids = [
                str(row["entry_id"])
                for row in structured_results
                if row.get("entry_id")
            ]
            for entry_id in semantic_ids:
                if entry_id in entries_by_id:
                    matched[entry_id] = entries_by_id[entry_id]
            retrieval_metadata = {
                str(row["entry_id"]): row
                for row in structured_results
                if row.get("entry_id")
            }
        if self.retrieval is not None and supports_vector:
            try:
                resident_result = await self.db.execute(
                    select(CodexEntry.id).where(
                        CodexEntry.project_id == project_id,
                        CodexEntry.status == "confirmed",
                        CodexEntry.resident,
                    )
                )
                resident_ids = list(resident_result.scalars().all())
                semantic = await self.retrieval.retrieve_similar_entities_l3(
                    self.db,
                    project_id,
                    query_text,
                    top_k=8,
                    threshold=0.62,
                    exclude_entry_ids=[*matched, *resident_ids],
                )
                semantic_ids = [row["entry_id"] for row in semantic]
                for entry_id in semantic_ids:
                    if entry_id in entries_by_id:
                        matched[entry_id] = entries_by_id[entry_id]
                retrieval_metadata.update(
                    {
                        str(row["entry_id"]): row
                        for row in semantic
                        if row.get("entry_id")
                    }
                )
            except Exception:
                # Exact retrieval remains useful if the embedding gateway is unavailable.
                pass

        lines: list[str] = []
        items: list[dict] = []
        state_by_entry = await latest_author_states(
            self.db,
            project_id=project_id,
            chapter_id=chapter_id,
            entry_ids=list(matched),
        )
        relations_by_entry = await self._outgoing_relation_lines(project_id, list(matched))
        for entry in matched.values():
            text = f"## {entry.name} ({entry.kind})\n{entry.description}"
            if entry.attrs:
                text += f"\n属性: {json.dumps(entry.attrs, ensure_ascii=False, sort_keys=True)}"
            if relations_by_entry.get(entry.id):
                text += "\n关系:\n" + "\n".join(relations_by_entry[entry.id])
            if state_by_entry.get(entry.id):
                current = "；".join(
                    f"{state['state_key']}={state['value']}（第{state['chapter_index']}章）"
                    for state in state_by_entry[entry.id]
                )
                text += f"\n当前状态: {current}"
            lines.append(text)
            metadata = retrieval_metadata.get(entry.id, {})
            items.append(
                {
                    "id": entry.id,
                    "name": entry.name,
                    "kind": entry.kind,
                    "source": metadata.get("source", "exact_name_or_alias"),
                    "reason": metadata.get("reason", "outline_reference"),
                    "score": metadata.get("score", 1.0),
                }
            )
        content = "\n\n".join(lines)
        return ContextLayer("retrieved", content, self.tokenizer.count(content), items)

    async def _build_layer3_summary(
        self,
        project_id: str,
        chapter_id: str,
        retrieval_nodes: Optional[list[str]] = None,
        *,
        structured_results: list[dict] | None = None,
    ) -> ContextLayer:
        """
        Layer 3: 前情摘要
        - 当前卷的已有章节摘要（200字/章）
        - 更早的卷摘要（每10章压缩一次）
        """
        current_result = await self.db.execute(
            select(Chapter).where(Chapter.id == chapter_id, Chapter.deleted_at.is_(None))
        )
        current = current_result.scalar_one_or_none()
        if current is None or current.project_id != project_id:
            return ContextLayer(key="summary", content="", tokens=0, items=[])

        lines: list[str] = []
        items: list[dict] = []

        # Earlier volume summaries are more compact than replaying every old chapter.
        if current.volume_id:
            volume_result = await self.db.execute(
                select(Volume)
                .where(Volume.project_id == project_id, Volume.deleted_at.is_(None))
                .order_by(Volume.idx)
            )
            volumes = list(volume_result.scalars().all())
            current_volume = next((volume for volume in volumes if volume.id == current.volume_id), None)
            for volume in volumes:
                if current_volume is None or volume.idx >= current_volume.idx:
                    break
                summary = await self._latest_summary("volume", volume.id) or volume.summary
                if summary:
                    lines.append(f"## 卷摘要：{volume.title}\n{summary}")
                    items.append({"id": volume.id, "name": volume.title, "kind": "volume"})

        chapters_result = await self.db.execute(
            select(Chapter)
            .where(
                Chapter.project_id == project_id,
                Chapter.deleted_at.is_(None),
                Chapter.idx < current.idx,
            )
            .order_by(Chapter.idx)
        )
        previous = list(chapters_result.scalars().all())
        if structured_results is not None:
            for row in structured_results:
                if not row.get("chunk_id") or not row.get("content_text"):
                    continue
                label = (
                    f"第{row['chapter_index']}章"
                    if row.get("chapter_index") is not None
                    else "前文"
                )
                lines.append(f"## 正文远距证据 · {label}\n{row['content_text']}")
                items.append(
                    {
                        "id": f"chunk:{row['chunk_id']}",
                        "name": f"{label} 正文分块",
                        "kind": "chapter_chunk",
                        "source": row.get("source"),
                        "reason": row.get("reason"),
                        "chapterId": row.get("chapter_id"),
                        "bodyRev": row.get("body_rev"),
                        "similarity": row.get("similarity"),
                        "contentHash": row.get("content_hash"),
                    }
                )
        elif self.retrieval is not None and retrieval_nodes:
            bind = self.db.get_bind()
            if bind is not None and bind.dialect.name == "postgresql" and previous:
                query_text = "\n".join(node.strip() for node in retrieval_nodes if node.strip())
                if query_text:
                    try:
                        semantic = await self.retrieval.retrieve_chapter_chunks_l3(
                            self.db,
                            project_id,
                            query_text,
                            top_k=6,
                            threshold=0.58,
                            chapter_ids=[chapter.id for chapter in previous],
                        )
                        chapter_labels = {chapter.id: f"第{chapter.idx}章" for chapter in previous}
                        for row in semantic:
                            label = chapter_labels.get(row["chapter_id"], "前文")
                            lines.append(f"## 正文远距证据 · {label}\n{row['content_text']}")
                            items.append(
                                {
                                    "id": f"chunk:{row['chunk_id']}",
                                    "name": f"{label} 正文分块",
                                    "kind": "chapter_chunk",
                                    "chapterId": row["chapter_id"],
                                    "bodyRev": row["body_rev"],
                                    "similarity": row["similarity"],
                                }
                            )
                    except Exception:
                        # Index failures must never block generation; summaries and
                        # adjacent text remain the safe fallback.
                        pass
        # Current-volume chapter summaries carry recent causal state.  Without a volume,
        # retain the latest 20 to keep the layer bounded before exact token trimming.
        same_volume = [chapter for chapter in previous if chapter.volume_id == current.volume_id]
        summary_result = await self.db.execute(
            select(DocumentSummary.owner_id, DocumentSummary.content)
            .join(ChapterBody, ChapterBody.chapter_id == DocumentSummary.owner_id)
            .where(
                DocumentSummary.owner_type == "chapter",
                DocumentSummary.owner_id.in_([chapter.id for chapter in same_volume]),
                DocumentSummary.status == "active",
                DocumentSummary.source_rev == ChapterBody.rev,
            )
            .order_by(DocumentSummary.owner_id, DocumentSummary.source_rev.desc())
        )
        summary_by_chapter: dict[str, str] = {}
        for owner_id, content in summary_result.all():
            summary_by_chapter.setdefault(owner_id, content)
        summarized_chapters = [
            (chapter, summary_by_chapter.get(chapter.id) or chapter.summary)
            for chapter in same_volume
            if summary_by_chapter.get(chapter.id) or chapter.summary
        ]
        for chapter, summary in summarized_chapters[-20:]:
            if summary:
                lines.append(f"## 第{chapter.idx}章 {chapter.title}\n{summary}")
                items.append({"id": chapter.id, "name": chapter.title, "kind": "chapter"})

        content = "\n\n".join(lines)
        return ContextLayer("summary", content, self.tokenizer.count(content), items)

    async def _retrieve_structured_evidence(
        self,
        project_id: str,
        chapter_id: str,
        retrieval_nodes: list[str],
        *,
        near_window: int,
        top_k: int,
    ) -> list[dict] | None:
        """Build bounded semantic groups and retrieve distant evidence once."""
        if self.retrieval is None:
            return None
        bind = self.db.get_bind()
        if bind is None or bind.dialect.name != "postgresql":
            return None
        query_text = "\n".join(node.strip() for node in retrieval_nodes if node.strip())
        if not query_text:
            return []

        entries = list(
            (
                await self.db.execute(
                    select(CodexEntry)
                    .options(selectinload(CodexEntry.aliases))
                    .where(
                        CodexEntry.project_id == project_id,
                        CodexEntry.status == "confirmed",
                    )
                    .order_by(CodexEntry.id)
                )
            )
            .scalars()
            .unique()
            .all()
        )
        group_for_kind = {
            "character": "characters",
            "location": "locations",
            "item": "items",
            "event": "foreshadows",
        }
        queries: dict[str, list[str]] = {
            "scenes": [node.strip() for node in retrieval_nodes if node.strip()][:12]
        }
        for entry in entries:
            group = group_for_kind.get(entry.kind)
            if group is None:
                continue
            if entry.name in query_text or any(
                alias.alias and alias.alias in query_text for alias in entry.aliases
            ):
                queries.setdefault(group, []).append(entry.name)
        foreshadow_markers = ("伏笔", "悬念", "秘密", "线索", "未解", "谜")
        marked_nodes = [
            node.strip()
            for node in retrieval_nodes
            if node.strip() and any(marker in node for marker in foreshadow_markers)
        ]
        if marked_nodes:
            queries.setdefault("foreshadows", []).extend(marked_nodes)
        try:
            return await self.retrieval.retrieve_structured_context(
                self.db,
                project_id,
                queries,
                current_chapter_id=chapter_id,
                top_k=top_k,
                near_window=near_window,
            )
        except Exception:
            # Summaries, exact in-process matching and adjacent prose remain
            # available when embeddings or pgvector are temporarily unavailable.
            return None

    async def _build_layer4_adjacent(self, chapter_id: str, *, limit: int = 2) -> ContextLayer:
        """
        Layer 4: 相邻原文
        - 最近若干章全文，数量由上下文模式决定
        - 从章末往前截取（开头往往是过渡，章末才是情节推进）
        """
        current_result = await self.db.execute(
            select(Chapter).where(Chapter.id == chapter_id, Chapter.deleted_at.is_(None))
        )
        current = current_result.scalar_one_or_none()
        if current is None:
            return ContextLayer(key="adjacent", content="", tokens=0, items=[])

        result = await self.db.execute(
            select(Chapter, ChapterBody)
            .join(ChapterBody, ChapterBody.chapter_id == Chapter.id)
            .where(
                Chapter.project_id == current.project_id,
                Chapter.deleted_at.is_(None),
                Chapter.idx < current.idx,
            )
            .order_by(Chapter.idx.desc())
            .limit(limit)
        )
        rows = list(result.all())
        # Present prose in chronological order while taking the nearest two chapters.
        rows.reverse()
        lines: list[str] = []
        items: list[dict] = []
        for chapter, body in rows:
            plain = html_to_text(body.content_html)
            if not plain:
                continue
            lines.append(f"## 第{chapter.idx}章 {chapter.title}\n{plain}")
            items.append({"id": chapter.id, "name": chapter.title, "kind": "chapter"})
        content = "\n\n".join(lines)
        return ContextLayer("adjacent", content, self.tokenizer.count(content), items)

    async def _latest_summary(self, owner_type: str, owner_id: str) -> Optional[str]:
        result = await self.db.execute(
            select(DocumentSummary.content)
            .where(
                DocumentSummary.owner_type == owner_type,
                DocumentSummary.owner_id == owner_id,
                DocumentSummary.status == "active",
            )
            .order_by(DocumentSummary.source_rev.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    def _trim_layer(self, layer: ContextLayer, target_tokens: int, *, keep_tail: bool = False) -> ContextLayer:
        """Pack complete lines/sentences into a token budget without token slicing."""
        if target_tokens <= 0:
            return ContextLayer(
                key=layer.key,
                content="",
                tokens=0,
                items=[],
                available_tokens=layer.tokens,
                budget_tokens=max(0, target_tokens),
                trim_reason="budget_limit" if layer.tokens else None,
            )

        if layer.tokens <= target_tokens:
            return layer

        marker = "[前文已截断]\n" if keep_tail else "\n[已截断]"
        marker_tokens = self.tokenizer.count(marker)
        if target_tokens <= marker_tokens:
            return ContextLayer(
                layer.key,
                "",
                0,
                [],
                available_tokens=layer.tokens,
                budget_tokens=target_tokens,
                trim_reason="budget_limit",
            )

        units = self._packing_units(layer.content)
        if keep_tail:
            units.reverse()
        chosen: list[str] = []
        remaining_tokens = target_tokens - marker_tokens
        separator_tokens = self.tokenizer.count("\n")
        for unit in units:
            unit_tokens = self.tokenizer.count(unit) + (separator_tokens if chosen else 0)
            if unit_tokens <= remaining_tokens:
                chosen = [unit, *chosen] if keep_tail else [*chosen, unit]
                remaining_tokens -= unit_tokens
                continue

            # Stored prose and summaries may contribute complete sentences from
            # an oversized block. Codex/resident entries stay atomic.
            if layer.key not in {"summary", "adjacent"}:
                continue
            heading, body = self._split_heading(unit)
            sentences = [part.strip() for part in re.split(r"(?<=[。！？!?；;])", body) if part.strip()]
            if len(sentences) <= 1:
                if layer.key == "adjacent" and remaining_tokens > 0:
                    fragment = self._fit_text_boundary(
                        body,
                        remaining_tokens - (separator_tokens if chosen else 0),
                        keep_tail=keep_tail,
                    )
                    if fragment:
                        chosen = [fragment, *chosen] if keep_tail else [*chosen, fragment]
                continue
            iterable = reversed(sentences) if keep_tail else sentences
            sentence_choice: list[str] = []
            for sentence in iterable:
                next_choice = [sentence, *sentence_choice] if keep_tail else [*sentence_choice, sentence]
                fragment = "".join(next_choice)
                if heading:
                    fragment = f"{heading}\n{fragment}"
                fragment_tokens = self.tokenizer.count(fragment) + (
                    separator_tokens if chosen else 0
                )
                if fragment_tokens > remaining_tokens:
                    break
                sentence_choice = next_choice
            if sentence_choice:
                fragment = "".join(sentence_choice)
                if heading:
                    fragment = f"{heading}\n{fragment}"
                chosen = [fragment, *chosen] if keep_tail else [*chosen, fragment]

        selected_content = "\n".join(chosen)
        trimmed_content = marker + selected_content if keep_tail else selected_content + marker
        trimmed_tokens = self.tokenizer.count(trimmed_content)
        # Tokenizers may merge across line boundaries. Remove complete units
        # until the exact final encoding fits; never decode a partial token run.
        while chosen and trimmed_tokens > target_tokens:
            chosen.pop(0 if keep_tail else -1)
            selected_content = "\n".join(chosen)
            trimmed_content = marker + selected_content if keep_tail else selected_content + marker
            trimmed_tokens = self.tokenizer.count(trimmed_content)
        ratio = target_tokens / layer.tokens

        return ContextLayer(
            key=layer.key,
            content=trimmed_content,
            tokens=trimmed_tokens,
            items=(
                layer.items[-max(1, int(len(layer.items) * ratio)) :]
                if keep_tail and layer.items
                else layer.items[: int(len(layer.items) * ratio)]
            ),
            available_tokens=layer.tokens,
            budget_tokens=target_tokens,
            trim_reason="budget_limit",
        )

    @staticmethod
    def _packing_units(content: str) -> list[str]:
        """Keep each heading-led Codex/chapter block intact during packing."""
        normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            return []
        units = [
            unit.strip()
            for unit in re.split(r"\n(?=##?\s)", normalized)
            if unit.strip()
        ]
        return units

    @staticmethod
    def _split_heading(unit: str) -> tuple[str, str]:
        first, separator, rest = unit.partition("\n")
        return (first, rest) if separator and first.startswith("#") else ("", unit)

    def _fit_text_boundary(self, content: str, budget: int, *, keep_tail: bool) -> str:
        """Fit an unpunctuated prose fallback at Unicode character boundaries."""
        if budget <= 0 or not content:
            return ""
        low, high = 0, len(content)
        while low < high:
            size = (low + high + 1) // 2
            candidate = content[-size:] if keep_tail else content[:size]
            if self.tokenizer.count(candidate) <= budget:
                low = size
            else:
                high = size - 1
        return content[-low:] if keep_tail and low else content[:low]
