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
from pydantic import BaseModel, Field, ValidationError

from config import settings
from services.chunking import TextChunk, chunk_html


class ProviderResponseError(RuntimeError):
    """模型网关返回了无法解析的响应。

    必须抛出而不是当成「没有 claim」返回空列表 —— 否则一次网关故障会被
    误读成「本章没有事实」，进而把已有 claim 判成过期。
    """


# Pydantic models for structured extraction
class ClaimOutput(BaseModel):
    """Structured claim from LLM extraction"""

    subject_text: str = Field(..., min_length=1, max_length=200)
    predicate: str = Field(..., min_length=1, max_length=100)
    object_type: str = Field(..., pattern="^(scalar|entity|location|ability|timestamp)$")
    object_value: Optional[str] = Field(None, max_length=1000)
    polarity: str = Field(default="positive", pattern="^(positive|negative)$")
    certainty: str = Field(default="explicit", pattern="^(explicit|inferred|uncertain)$")
    paragraph_id: Optional[str] = Field(None, max_length=100)
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)


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
        """Build extraction prompt with schema"""
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
      "confidence": 0.9
    }}
  ]
}}

Valid object_type values: scalar, entity, location, ability, timestamp
Valid polarity values: positive, negative
Valid certainty values: explicit, inferred, uncertain

Content:
{content}"""
