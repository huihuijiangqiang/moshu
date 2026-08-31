"""
LLM providers for consistency extraction and summarization
Uses model_gateway configuration with injectable httpx client

关于长文本：早期实现用 content_html[:8000] / [:4000] 截断，超长章节的后半部分
永远不会被抽取或摘要，冲突会被静默漏掉。现在统一走 services.chunking 做确定性
分块，逐块调用模型后聚合，保证全文覆盖。

关于模型选择：网关 URL / key / model id 全部来自 config.settings，不在代码里
硬编码具体模型名。
"""
import json
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field, ValidationError, model_validator

from config import settings
from services.chunking import TextChunk, chunk_html
from services.claim_identity import claim_fingerprint
from services.timeline import GLOBAL_ORDER_BASES, parse_absolute_anchor

#: 指纹计算只有一份实现（services.claim_identity），这里保留旧的导入位置。
__all__ = [
    "ClaimOutput",
    "ConsistencyProvider",
    "ExtractionResponse",
    "ProviderResponseError",
    "claim_fingerprint",
]


class ProviderResponseError(RuntimeError):
    """模型网关返回了无法解析的响应。

    必须抛出而不是当成「没有 claim」返回空列表 —— 否则一次网关故障会被
    误读成「本章没有事实」，进而把已有 claim 判成过期。
    """


# Pydantic models for structured extraction
class ClaimOutput(BaseModel):
    """Structured claim from LLM extraction

    关于叙事顺序，这里的契约是「模型报证据，服务定顺序」（架构 4.3）：

    抽取是逐块进行的，模型看不到整章，更看不到整个项目。让它直接给 story_order，
    它只能在当前块内自行编号 —— 而规则扫描是全项目范围的，会把不同块给出的数字
    直接放在一起排序。局部序号被当成全局序号，等于换一种方式伪造顺序。

    所以模型只报**可追溯到正文的证据**：那句时间表述的原文、它归一化后的绝对
    时间、或者它相对于哪个锚点的先后关系。story_order 由 services.timeline 依据
    已确认的全局锚点统一分配；没有共同锚点就保持 NULL，claim 只进待确认列表。

    story_order / valid_from_order / valid_to_order 仍然接受，但只在锚点全局可比
    时才被采纳，且 story_order 一律由时间线服务按锚点重算 —— 见
    ``_drop_locally_scoped_order``。
    """

    subject_text: str = Field(..., min_length=1, max_length=200)
    predicate: str = Field(..., min_length=1, max_length=100)
    object_type: str = Field(..., pattern="^(scalar|entity|location|ability|timestamp)$")
    object_value: Optional[str] = Field(None, max_length=1000)
    polarity: str = Field(default="positive", pattern="^(positive|negative)$")
    certainty: str = Field(default="explicit", pattern="^(explicit|inferred|uncertain)$")
    paragraph_id: Optional[str] = Field(None, max_length=100)
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)

    #: 故事时间线标识。多线叙事先按 timeline_id 隔离，只有存在已确认跨线锚点时
    #: 才比较（架构 4.3）。模型不确定时留空，由调用方决定是否归入主线。
    timeline_id: Optional[str] = Field(None, max_length=32)

    #: 这条事实所在处的全局段落号（提示词里的 [P<n>] 标号）。
    #: 参与身份指纹：同一时间线里不同段落的同语义陈述是两次独立出现，
    #: 而重叠区域里同一段在相邻两块中拿到同一个号，所以重复抽取仍会被去重。
    source_anchor: Optional[str] = Field(None, max_length=64)

    #: 时间锚点的**原文**。可追溯性的关键：作者和后续人工确认都能拿它回正文核对。
    temporal_anchor_text: Optional[str] = Field(None, max_length=200)

    #: 锚点归一化后的绝对时间（ISO-8601）。只有它能提供跨块可比的共同标尺。
    #: 自然语言表述（「三日后的清晨」）无法归一化时必须留空。
    temporal_anchor_value: Optional[str] = Field(None, max_length=64)

    #: 相对关系及其参照锚点 —— 相对表述的证据。MVP 不据此分配 story_order，
    #: 但必须收下来，否则这条信息就永久丢失了。
    temporal_relation: Optional[str] = Field(
        None, pattern="^(before|after|simultaneous)$"
    )
    temporal_relation_ref: Optional[str] = Field(None, max_length=200)

    #: 顺序依据。absolute_datetime 是 MVP 里唯一的全局锚点；
    #: narration_local 明确表示「只在本块内有意义」，绝不能当全局序号用。
    order_basis: Optional[str] = Field(
        None, pattern="^(absolute_datetime|relative_to_anchor|narration_local|unknown)$"
    )
    order_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)

    #: 顺序值。由 services.timeline 写入；模型给的值只在全局可比时才保留，
    #: 且 story_order 会被按锚点重算。
    story_order: Optional[float] = Field(None)
    valid_from_order: Optional[float] = Field(None)
    valid_to_order: Optional[float] = Field(None)

    @model_validator(mode="after")
    def _drop_locally_scoped_order(self) -> "ClaimOutput":
        """丢掉没有全局标尺的顺序值，而不是让它冒充全局序号流下去。

        判据是 order_basis：只有 absolute_datetime 且带得动一个可解析的
        temporal_anchor_value，才算全局可比。narration_local 是最需要拦住的一类
        —— 它的数字在块内自洽，跨块毫无意义，而规则会跨块比较。

        宁可漏报也不误报：伪造的顺序会让倒叙被判成矛盾，而作者看到的是一条
        无法解释的高等级告警。清空后 claim 仍会落库，只是不进硬规则。
        """
        globally_comparable = (
            self.order_basis in GLOBAL_ORDER_BASES
            and parse_absolute_anchor(self.temporal_anchor_value) is not None
        )
        if not globally_comparable:
            self.story_order = None
            self.valid_from_order = None
            self.valid_to_order = None
            return self
        # 区间反了说明模型没算清楚，整段区间都不可信
        if (
            self.valid_from_order is not None
            and self.valid_to_order is not None
            and self.valid_to_order <= self.valid_from_order
        ):
            self.valid_from_order = None
            self.valid_to_order = None
        return self


class ExtractionResponse(BaseModel):
    """Response from extraction endpoint"""

    claims: list[ClaimOutput] = Field(default_factory=list)


class ConsistencyProvider:
    """Provider for consistency extraction and summarization using model_gateway"""

    def __init__(
        self,
        client: Optional[httpx.AsyncClient] = None,
        *,
        gateway_tier: Optional[str] = None,
    ):
        """
        Args:
            client: Injectable httpx client for testing
            gateway_tier: 覆盖默认网关档位（cheap/main/premium）
        """
        self._client = client
        self._gateway_tier = gateway_tier
        self.extractor_version = "1.0.0"

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create httpx client"""
        if self._client:
            return self._client
        return httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))

    @property
    def gateway_url(self) -> str:
        return settings.gateway_url(self._gateway_tier)

    @property
    def gateway_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {settings.gateway_key(self._gateway_tier)}",
            "Content-Type": "application/json",
        }

    def split_content(self, content_html: str) -> list[TextChunk]:
        """按配置切块；全文覆盖，不截断。"""
        return chunk_html(
            content_html,
            max_chars=settings.consistency_chunk_chars,
            overlap_chars=settings.consistency_chunk_overlap_chars,
            max_chunks=settings.consistency_max_chunks,
        )

    async def extract_claims(
        self,
        content_html: str,
        project_id: str,
        chapter_id: str,
    ) -> list[dict[str, Any]]:
        """
        Extract structured claims from chapter body

        全文分块后逐块抽取并聚合；同一指纹只保留置信度最高的一条。

        Args:
            content_html: Chapter HTML content
            project_id: Project ID for context
            chapter_id: Chapter ID

        Returns:
            List of claims with fingerprints

        Raises:
            httpx.HTTPError: 网关请求失败
            ProviderResponseError: 网关响应无法解析
        """
        chunks = self.split_content(content_html)
        if not chunks:
            return []

        client = await self._get_client()
        try:
            aggregated: dict[str, dict[str, Any]] = {}
            for chunk in chunks:
                for claim_dict in await self._extract_chunk(client, chunk):
                    fingerprint = claim_dict["fingerprint"]
                    existing = aggregated.get(fingerprint)
                    if existing is None or claim_dict["confidence"] > existing["confidence"]:
                        aggregated[fingerprint] = claim_dict
            # dict 保序 —— 输出顺序与首次出现顺序一致，保证结果确定
            return list(aggregated.values())
        finally:
            if not self._client:
                await client.aclose()

    async def _extract_chunk(
        self, client: httpx.AsyncClient, chunk: TextChunk
    ) -> list[dict[str, Any]]:
        """抽取单个块。"""
        response = await client.post(
            self.gateway_url,
            headers=self.gateway_headers,
            json={
                "model": settings.consistency_extraction_model,
                "messages": [
                    {
                        "role": "system",
                        "content": "Extract factual claims from narrative text. Return valid JSON only.",
                    },
                    {
                        "role": "user",
                        "content": self._build_extraction_prompt(chunk.labeled_text()),
                    },
                ],
                "temperature": 0.0,
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()

        content = self._message_content(response, context=f"extraction chunk {chunk.index}")
        try:
            data = json.loads(content)
            extraction = ExtractionResponse.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ProviderResponseError(
                f"extraction chunk {chunk.index} returned unparseable payload: {exc}"
            ) from exc

        claims = []
        for claim in extraction.claims:
            claim_dict = claim.model_dump()
            claim_dict["fingerprint"] = claim_fingerprint(
                subject_text=claim.subject_text,
                predicate=claim.predicate,
                object_type=claim.object_type,
                object_value=claim.object_value,
                polarity=claim.polarity,
                timeline_id=claim.timeline_id,
                source_anchor=claim.source_anchor,
            )
            claim_dict["chunk_index"] = chunk.index
            claims.append(claim_dict)
        return claims

    async def generate_summary(
        self,
        content_html: str,
        summary_type: str = "chapter",
    ) -> tuple[str, int]:
        """
        Generate chapter or volume summary

        超长内容先逐块摘要，再把各块摘要合并成一份总摘要（map-reduce），
        全文都会进入模型，不再截断前 4000 字符。

        Args:
            content_html: Content to summarize
            summary_type: "chapter" or "volume"

        Returns:
            Tuple of (summary_text, estimated_token_count)

        Raises:
            httpx.HTTPError: 网关请求失败
            ProviderResponseError: 网关响应无法解析
        """
        max_words = 150 if summary_type == "chapter" else 500
        chunks = self.split_content(content_html)
        if not chunks:
            return "", 0

        client = await self._get_client()
        try:
            if len(chunks) == 1:
                return await self._summarize_text(
                    client,
                    chunks[0].text,
                    summary_type=summary_type,
                    max_words=max_words,
                )

            total_tokens = 0
            partials = []
            for chunk in chunks:
                text, tokens = await self._summarize_text(
                    client,
                    chunk.text,
                    summary_type=f"{summary_type} section",
                    max_words=max_words,
                )
                partials.append(text)
                total_tokens += tokens

            combined, tokens = await self._summarize_text(
                client,
                "\n\n".join(partials),
                summary_type=summary_type,
                max_words=max_words,
            )
            return combined, total_tokens + tokens
        finally:
            if not self._client:
                await client.aclose()

    async def _summarize_text(
        self,
        client: httpx.AsyncClient,
        text: str,
        *,
        summary_type: str,
        max_words: int,
    ) -> tuple[str, int]:
        """一次摘要调用。"""
        prompt = f"Summarize the following {summary_type} in {max_words} words or less:\n\n{text}"
        response = await client.post(
            self.gateway_url,
            headers=self.gateway_headers,
            json={
                "model": settings.consistency_summary_model,
                "messages": [
                    {"role": "system", "content": "You are a concise summarization assistant."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 300,
            },
        )
        response.raise_for_status()

        summary_text = self._message_content(response, context="summary").strip()
        payload = response.json()
        usage = payload.get("usage") or {}
        token_count = usage.get("total_tokens", len(summary_text) // 4)
        return summary_text, token_count

    @staticmethod
    def _message_content(response: httpx.Response, *, context: str) -> str:
        """从 chat completion 响应里取文本，结构不对就抛错（不返回空串）。"""
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderResponseError(f"{context}: response body is not JSON") from exc

        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderResponseError(
                f"{context}: response missing choices[0].message.content"
            ) from exc

        if not isinstance(content, str):
            raise ProviderResponseError(f"{context}: message content is not a string")
        return content

    def _build_extraction_prompt(self, content: str) -> str:
        """Build extraction prompt with schema

        关于顺序：**绝不要求模型给顺序编号**。抽取逐块进行，模型看不到整章，任何
        它编出来的数字都只在本块内自洽；而规则扫描是全项目范围的，会把不同块的
        数字直接放在一起排序。旧提示词说「use any increasing numbers」，等于让每
        个块各造一套标尺，再由下游当成全局序号比较 —— 换一种方式伪造顺序。

        这里改成要模型报**可追溯的时间证据**：那句时间表述的原文、归一化后的绝对
        时间、或相对关系。story_order 由 services.timeline 依据全局锚点统一分配。
        """
        return f"""Extract factual claims from this narrative text. Return JSON with this exact structure:

{{
  "claims": [
    {{
      "subject_text": "character or entity name",
      "predicate": "alive|dead|owns|knows|located_at|has_ability|etc",
      "object_type": "scalar|entity|location|ability|timestamp",
      "object_value": "the value or null",
      "polarity": "positive|negative",
      "certainty": "explicit|inferred|uncertain",
      "paragraph_id": "optional paragraph identifier",
      "source_anchor": "the [P<n>] number of the paragraph this fact comes from",
      "confidence": 0.9,
      "timeline_id": "story timeline identifier, or null if unclear",
      "temporal_anchor_text": "the exact time expression quoted from the text, or null",
      "temporal_anchor_value": "that time normalized as ISO-8601, or null",
      "temporal_relation": "before|after|simultaneous, or null",
      "temporal_relation_ref": "what the relation is measured against, or null",
      "order_basis": "absolute_datetime|relative_to_anchor|narration_local|unknown",
      "order_confidence": 0.0
    }}
  ]
}}

Valid object_type values: scalar, entity, location, ability, timestamp
Valid polarity values: positive, negative
Valid certainty values: explicit, inferred, uncertain

SOURCE ANCHORS:

Each paragraph is prefixed with a marker like [P7]. Set `source_anchor` to that
number for the paragraph the fact comes from. These numbers are stable across the
whole chapter, so the same fact stated in two different paragraphs stays two
separate records, each pointing at its own place in the text, while the same
paragraph seen twice (excerpts overlap) stays one record.

CRITICAL RULES FOR NARRATIVE ORDER:

DO NOT output any order numbers. You are reading one excerpt of a longer chapter
and cannot see the rest, so any numbering you invent would only be meaningful
inside this excerpt. Ordering is computed later from the evidence you report here.

Instead, report the time evidence the text actually gives:

- `temporal_anchor_text`: quote the time expression verbatim ("三日后的清晨",
  "元和七年冬"). This is what a human checks against the text later.
- `temporal_anchor_value`: the SAME time normalized to ISO-8601
  (e.g. "0812-11-03" or "2024-03-01T18:30"). Only fill this in if the text pins
  down an actual date or time. If the text only says "three days later" with no
  fixed date anywhere, leave this null and use `temporal_relation` instead.
- `temporal_relation` / `temporal_relation_ref`: for relative statements, say
  whether this event is before/after/simultaneous with what ("李长风下山之后").

Set `order_basis` to the strongest evidence you have:
- "absolute_datetime": the text states a date or time you normalized above.
- "relative_to_anchor": only a relative expression, no absolute time.
- "narration_local": you only know the order within THIS excerpt. This is not
  usable for comparison across the chapter, so report it honestly as such.
- "unknown": you cannot tell where this sits in story time.

DO NOT GUESS. A wrong order produces a false contradiction shown to the author;
absent order information is handled safely as "needs confirmation". In particular,
a flashback narrated late happens early in the story -- if you cannot anchor it to
an absolute time, say "relative_to_anchor" or "unknown" rather than inventing a
position for it.

Set `order_confidence` to how sure you are about the time evidence (0.0-1.0). Use a
low value when the text is ambiguous rather than asserting a confident anchor.

Use `timeline_id` to separate independent narrative threads (e.g. parallel POVs).
Leave it null if the text does not make the thread clear. Never compare events
across different timelines.

Content:
{content}"""
