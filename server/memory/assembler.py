"""
四层上下文装配器 - 整个系统的核心
装配顺序和字节稳定性直接影响 prompt cache 命中率
"""
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_codex import CodexEntry


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

    def __init__(self, db: AsyncSession, tokenizer):
        self.db = db
        self.tokenizer = tokenizer

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
        total = layer1.tokens + layer2.tokens + layer3.tokens + layer4.tokens
        trimmed = []

        if total > self.BUDGET_TOTAL:
            # 按 layer4 → layer3 → layer2 顺序削减，layer1 永不削
            overage = total - self.BUDGET_TOTAL

            # 先削 layer4
            if layer4.tokens > overage:
                layer4 = self._trim_layer(layer4, layer4.tokens - overage)
                trimmed.append("layer4")
                overage = 0
            else:
                overage -= layer4.tokens
                layer4 = self._trim_layer(layer4, 0)
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

            total = self.BUDGET_TOTAL

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
            .where(CodexEntry.project_id == project_id, CodexEntry.resident)
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
                text += f"属性: {entry.attrs}\n"
            lines.append(text)
            items.append({"id": entry.id, "name": entry.name, "kind": entry.kind})

        content = "\n".join(lines)
        tokens = self.tokenizer.count(content)

        return ContextLayer(key="resident", content=content, tokens=tokens, items=items)

    async def _build_layer2_retrieved(self, project_id: str, outline_nodes: list[str]) -> ContextLayer:
        """
        Layer 2: 检索条目
        - 从章纲提取实体
        - 先走 codex_aliases 精确命中
        - 未命中的走 pgvector 语义检索（暂未实现，返回空）
        """
        # TODO: 实体抽取器（guard/extractor.py）
        # 这里简化为直接返回空
        return ContextLayer(key="retrieved", content="", tokens=0, items=[])

    async def _build_layer3_summary(self, project_id: str, chapter_id: str) -> ContextLayer:
        """
        Layer 3: 前情摘要
        - 当前卷的已有章节摘要（200字/章）
        - 更早的卷摘要（每10章压缩一次）
        """
        # TODO: 根据 chapter_id 找到当前卷，查询前文摘要
        # 这里简化为返回空
        return ContextLayer(key="summary", content="", tokens=0, items=[])

    async def _build_layer4_adjacent(self, chapter_id: str) -> ContextLayer:
        """
        Layer 4: 相邻原文
        - 前2章全文
        - 从章末往前截取（开头往往是过渡，章末才是情节推进）
        """
        # TODO: 查询前2章，提取 content_html
        # 这里简化为返回空
        return ContextLayer(key="adjacent", content="", tokens=0, items=[])

    def _trim_layer(self, layer: ContextLayer, target_tokens: int) -> ContextLayer:
        """裁剪层内容到目标token数"""
        if target_tokens <= 0:
            return ContextLayer(key=layer.key, content="", tokens=0, items=[])

        if layer.tokens <= target_tokens:
            return layer

        # 简单按字符比例截断（实际应该按 token 精确截）
        ratio = target_tokens / layer.tokens
        cut_pos = int(len(layer.content) * ratio)
        trimmed_content = layer.content[:cut_pos] + "\n[已截断]"
        trimmed_tokens = self.tokenizer.count(trimmed_content)

        return ContextLayer(
            key=layer.key,
            content=trimmed_content,
            tokens=trimmed_tokens,
            items=layer.items[: int(len(layer.items) * ratio)],
        )
