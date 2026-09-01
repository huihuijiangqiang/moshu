"""
四层上下文装配器 - 整个系统的核心
装配顺序和字节稳定性直接影响 prompt cache 命中率
"""

import json
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models_codex import CodexEntry
from db.models_consistency_extended import DocumentSummary
from db.models_core import Chapter, ChapterBody, Volume
from services.retrieval import ConsistencyRetrieval


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


@dataclass
class ContextLayer:
    """单层上下文"""

    key: str  # resident, retrieved, summary, adjacent
    content: str
    tokens: int
    items: list[dict]  # 用于右栏展示


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


class ContextAssembler:
    """上下文装配器 - 四层预算分配与裁剪"""

    # 预算分配（单位：token）
    BUDGET_TOTAL = 25000
    BUDGET_LAYER1 = 6000  # 常驻设定，永不削减
    BUDGET_LAYER2 = 5000  # 检索条目
    BUDGET_LAYER3 = 4000  # 前情摘要
    BUDGET_LAYER4 = 10000  # 相邻原文

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
        # Layer 1: 常驻设定（全量，按 id 排序保证字节稳定）
        layer1 = await self._build_layer1_resident(project_id)

        # Layer 2: 检索条目（章纲实体抽取 → 精确命中 → 向量兜底）
        layer2 = await self._build_layer2_retrieved(project_id, outline_nodes)

        # Layer 3: 前情摘要（本卷章摘要 + 更早的卷摘要）
        layer3 = await self._build_layer3_summary(project_id, chapter_id)

        # Layer 4: 相邻原文（前2章，从章末往前截取）
        layer4 = await self._build_layer4_adjacent(chapter_id)

        # 预算检查与裁剪
        layer2 = self._trim_layer(layer2, self.BUDGET_LAYER2)
        layer3 = self._trim_layer(layer3, self.BUDGET_LAYER3)
        layer4 = self._trim_layer(layer4, self.BUDGET_LAYER4, keep_tail=True)

        total = layer1.tokens + layer2.tokens + layer3.tokens + layer4.tokens
        trimmed = []

        if total > self.BUDGET_TOTAL:
            # 按 layer4 → layer3 → layer2 顺序削减，layer1 永不削
            overage = total - self.BUDGET_TOTAL

            # 先削 layer4
            if layer4.tokens > overage:
                layer4 = self._trim_layer(layer4, layer4.tokens - overage, keep_tail=True)
                trimmed.append("layer4")
                overage = 0
            else:
                overage -= layer4.tokens
                layer4 = self._trim_layer(layer4, 0, keep_tail=True)
                trimmed.append("layer4")

            # 还超就削 layer3
            if overage > 0:
                if layer3.tokens > overage:
                    layer3 = self._trim_layer(layer3, layer3.tokens - overage)
                    trimmed.append("layer3")
                    overage = 0
                else:
                    overage -= layer3.tokens
                    layer3 = self._trim_layer(layer3, 0)
                    trimmed.append("layer3")

            # 还超就削 layer2
            if overage > 0:
                layer2 = self._trim_layer(layer2, max(0, layer2.tokens - overage))
                trimmed.append("layer2")

            total = layer1.tokens + layer2.tokens + layer3.tokens + layer4.tokens

        return AssembledContext(
            layer1_resident=layer1,
            layer2_retrieved=layer2,
            layer3_summary=layer3,
            layer4_adjacent=layer4,
            total_tokens=total,
            budget_exceeded=len(trimmed) > 0,
            trimmed_layers=trimmed,
        )

    async def _build_layer1_resident(self, project_id: str) -> ContextLayer:
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

        # 序列化为文本
        lines = []
        items = []
        for entry in entries:
            text = f"## {entry.name} ({entry.kind})\n{entry.description}\n"
            if entry.attrs:
                text += f"属性: {json.dumps(entry.attrs, ensure_ascii=False, sort_keys=True)}\n"
            lines.append(text)
            items.append({"id": entry.id, "name": entry.name, "kind": entry.kind})

        content = "\n".join(lines)
        tokens = self.tokenizer.count(content)

        return ContextLayer(key="resident", content=content, tokens=tokens, items=items)

    async def _build_layer2_retrieved(self, project_id: str, outline_nodes: list[str]) -> ContextLayer:
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
        matched = {
            entry.id: entry
            for entry in entries
            if entry.name in query_text or any(alias.alias and alias.alias in query_text for alias in entry.aliases)
        }

        bind = self.db.get_bind()
        supports_vector = bind is not None and bind.dialect.name == "postgresql"
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
                if semantic_ids:
                    semantic_result = await self.db.execute(select(CodexEntry).where(CodexEntry.id.in_(semantic_ids)))
                    by_id = {entry.id: entry for entry in semantic_result.scalars().all()}
                    for entry_id in semantic_ids:
                        if entry_id in by_id:
                            matched[entry_id] = by_id[entry_id]
            except Exception:
                # Exact retrieval remains useful if the embedding gateway is unavailable.
                pass

        lines: list[str] = []
        items: list[dict] = []
        for entry in matched.values():
            text = f"## {entry.name} ({entry.kind})\n{entry.description}"
            if entry.attrs:
                text += f"\n属性: {json.dumps(entry.attrs, ensure_ascii=False, sort_keys=True)}"
            lines.append(text)
            items.append({"id": entry.id, "name": entry.name, "kind": entry.kind})
        content = "\n\n".join(lines)
        return ContextLayer("retrieved", content, self.tokenizer.count(content), items)

    async def _build_layer3_summary(self, project_id: str, chapter_id: str) -> ContextLayer:
        """
        Layer 3: 前情摘要
        - 当前卷的已有章节摘要（200字/章）
        - 更早的卷摘要（每10章压缩一次）
        """
        current_result = await self.db.execute(select(Chapter).where(Chapter.id == chapter_id))
        current = current_result.scalar_one_or_none()
        if current is None or current.project_id != project_id:
            return ContextLayer(key="summary", content="", tokens=0, items=[])

        lines: list[str] = []
        items: list[dict] = []

        # Earlier volume summaries are more compact than replaying every old chapter.
        if current.volume_id:
            volume_result = await self.db.execute(
                select(Volume).where(Volume.project_id == project_id).order_by(Volume.idx)
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
            select(Chapter).where(Chapter.project_id == project_id, Chapter.idx < current.idx).order_by(Chapter.idx)
        )
        previous = list(chapters_result.scalars().all())
        # Current-volume chapter summaries carry recent causal state.  Without a volume,
        # retain the latest 20 to keep the layer bounded before exact token trimming.
        same_volume = [chapter for chapter in previous if chapter.volume_id == current.volume_id]
        summary_result = await self.db.execute(
            select(DocumentSummary.owner_id, DocumentSummary.content)
            .where(
                DocumentSummary.owner_type == "chapter",
                DocumentSummary.owner_id.in_([chapter.id for chapter in same_volume]),
                DocumentSummary.status == "active",
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

    async def _build_layer4_adjacent(self, chapter_id: str) -> ContextLayer:
        """
        Layer 4: 相邻原文
        - 前2章全文
        - 从章末往前截取（开头往往是过渡，章末才是情节推进）
        """
        current_result = await self.db.execute(select(Chapter).where(Chapter.id == chapter_id))
        current = current_result.scalar_one_or_none()
        if current is None:
            return ContextLayer(key="adjacent", content="", tokens=0, items=[])

        result = await self.db.execute(
            select(Chapter, ChapterBody)
            .join(ChapterBody, ChapterBody.chapter_id == Chapter.id)
            .where(Chapter.project_id == current.project_id, Chapter.idx < current.idx)
            .order_by(Chapter.idx.desc())
            .limit(2)
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
        """裁剪层内容到目标token数"""
        if target_tokens <= 0:
            return ContextLayer(key=layer.key, content="", tokens=0, items=[])

        if layer.tokens <= target_tokens:
            return layer

        marker = "[前文已截断]\n" if keep_tail else "\n[已截断]"
        marker_tokens = self.tokenizer.count(marker)
        if target_tokens <= marker_tokens:
            content = self.tokenizer.decode(self.tokenizer.encode(marker)[:target_tokens])
            return ContextLayer(layer.key, content, self.tokenizer.count(content), [])
        content_budget = max(0, target_tokens - marker_tokens)
        encoded = self.tokenizer.encode(layer.content)
        selected = encoded[-content_budget:] if keep_tail and content_budget else encoded[:content_budget]
        trimmed_content = (
            marker + self.tokenizer.decode(selected) if keep_tail else self.tokenizer.decode(selected) + marker
        )
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
        )
