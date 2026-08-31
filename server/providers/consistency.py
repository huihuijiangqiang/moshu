"""
LLM providers for consistency extraction and summarization
Uses model_gateway configuration with injectable httpx client

关于长文本：早期实现用 content_html[:8000] / [:4000] 截断，超长章节的后半部分
永远不会被抽取或摘要，冲突会被静默漏掉。现在统一走 services.chunking 做确定性
分块，逐块调用模型后聚合，保证全文覆盖。

关于模型选择：网关 URL / key / model id 全部来自 config.settings，不在代码里
硬编码具体模型名。
"""
import hashlib
import json
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field, ValidationError, model_validator

from config import settings
from services.chunking import TextChunk, chunk_html


class ProviderResponseError(RuntimeError):
    """模型网关返回了无法解析的响应。

    必须抛出而不是当成「没有 claim」返回空列表 —— 否则一次网关故障会被
    误读成「本章没有事实」，进而把已有 claim 判成过期。
    """


# Pydantic models for structured extraction
class ClaimOutput(BaseModel):
    """Structured claim from LLM extraction

    时间线字段（timeline_id / story_order / valid_*_order）全部可空，且**只在模型
    能从正文可靠判断时才填**。架构 4.3 规定 story_order 是故事世界中的事件顺序，
    不是「第几章」：倒叙、插叙、多线叙事里章节顺序与故事顺序会背离，用章节序号
    顶替会把正常倒叙判成时序矛盾。

    因此这里不给任何默认值 —— 无法可靠确定时保持 None，claim 只进入待确认，
    不参与依赖时序的硬规则（架构 4.3 第 124 行）。
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

    #: 同一时间线内的事件顺序。相对值，只要求同线内单调，不要求与章节顺序一致。
    story_order: Optional[float] = Field(None)

    #: 事实有效区间。valid_to_order 为空表示「至今仍有效」。
    valid_from_order: Optional[float] = Field(None)
    valid_to_order: Optional[float] = Field(None)

    #: 模型对叙事顺序的判断依据；order_confidence 低于阈值时顺序不予采信。
    order_basis: Optional[str] = Field(
        None, pattern="^(explicit_time|sequential_narration|flashback|unknown)$"
    )
    order_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _drop_unreliable_order(self) -> "ClaimOutput":
        """顺序不可靠时清空顺序字段，而不是让它带着 unknown 依据流下去。

        宁可漏报也不误报：伪造的顺序会让倒叙被判成矛盾，而作者看到的是一条
        无法解释的高等级告警。清空后 claim 仍会落库，只是不进硬规则。
        """
        if self.order_basis == "unknown":
            self.story_order = None
            self.valid_from_order = None
            self.valid_to_order = None
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


def claim_fingerprint(
    *,
    subject_text: str,
    predicate: str,
    object_type: str,
    object_value: Optional[str],
    polarity: str,
) -> str:
    """稳定的 claim 指纹（大小写、空白无关），用于跨块去重与数据库唯一键。"""
    fingerprint_data = {
        "subject_text": subject_text.lower().strip(),
        "predicate": predicate.lower().strip(),
        "object_type": object_type,
        "object_value": (object_value or "").lower().strip(),
        "polarity": polarity,
    }
    return hashlib.sha256(json.dumps(fingerprint_data, sort_keys=True).encode()).hexdigest()


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
                    {"role": "user", "content": self._build_extraction_prompt(chunk.text)},
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

        时间线字段必须在提示词里说清「不确定就留 null」。模型天然倾向于把字段填满，
        而伪造的 story_order 比留空危害大得多：倒叙会被判成时序矛盾，作者收到的是
        无法解释的高等级告警。story_order 是故事世界顺序，不是章节顺序。
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
      "confidence": 0.9,
      "timeline_id": "story timeline identifier, or null if unclear",
      "story_order": null,
      "valid_from_order": null,
      "valid_to_order": null,
      "order_basis": "explicit_time|sequential_narration|flashback|unknown",
      "order_confidence": 0.0
    }}
  ]
}}

Valid object_type values: scalar, entity, location, ability, timestamp
Valid polarity values: positive, negative
Valid certainty values: explicit, inferred, uncertain

CRITICAL RULES FOR NARRATIVE ORDER:

`story_order` is the order of events in the STORY WORLD, not the order they appear
in the text, and NOT the chapter number. A flashback narrated late in the chapter
happens EARLY in the story and must get a SMALLER story_order than the scene that
frames it. Only the relative order matters; use any increasing numbers.

Set `order_basis` to how you determined the order:
- "explicit_time": the text states a date, time, or explicit interval.
- "sequential_narration": events are plainly narrated one after another in story time.
- "flashback": this event is narrated out of order (memory, dream, retelling).
- "unknown": you cannot tell where this sits in story time.

If `order_basis` is "unknown", set story_order, valid_from_order and valid_to_order
to null. DO NOT GUESS. A wrong order produces a false contradiction shown to the
author; a null order is handled safely as "needs confirmation".

Set `order_confidence` to how sure you are about the ordering (0.0-1.0). Use a low
value when the text is ambiguous rather than inventing a confident order.

Use `timeline_id` to separate independent narrative threads (e.g. parallel POVs).
Leave it null if the text does not make the thread clear. Never compare events
across different timelines.

Content:
{content}"""
