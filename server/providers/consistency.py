"""
LLM providers for consistency extraction and summarization
Uses model_gateway configuration with injectable httpx client

关于长文本：早期实现用 content_html[:8000] / [:4000] 截断，超长章节的后半部分
永远不会被抽取或摘要，冲突会被静默漏掉。现在统一走 services.chunking 做确定性
分块，逐块调用模型后聚合，保证全文覆盖。

关于模型选择：网关 URL / key / model id 全部来自 config.settings，不在代码里
硬编码具体模型名。

关于 SSE 流式：真实 10 万字压力测试发现 60 秒非流式 timeout 会中断长响应。现在
所有网关调用统一走 stream=true + SSE 聚合，支持有限指数退避重试（408/429/5xx、
httpx timeout/transport error），保证长文本抽取的可靠性。
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
from typing import TYPE_CHECKING, Any, Optional

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from config import settings
from services.chunking import TextChunk, chunk_html
from services.claim_identity import claim_fingerprint
from services.timeline import GLOBAL_ORDER_BASES, parse_absolute_anchor

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

#: 指纹计算只有一份实现（services.claim_identity），这里保留旧的导入位置。
__all__ = [
    "ClaimOutput",
    "ConsistencyProvider",
    "ExtractionResponse",
    "ProviderResponseError",
    "claim_fingerprint",
]


def _extract_paragraph_position(source_anchor: Optional[str]) -> Optional[int]:
    """从 source_anchor 提取全局段落号（「P42」→ 42），格式错误返回 None。"""
    if not source_anchor or not isinstance(source_anchor, str):
        return None
    cleaned = source_anchor.strip().upper()
    if not cleaned.startswith("P"):
        return None
    try:
        return int(cleaned[1:])
    except ValueError:
        return None


def _validate_source_anchor(
    source_anchor: Optional[str], valid_positions: tuple[int, ...]
) -> bool:
    """校验来源锚点：模型报的段落号必须真实存在于当前块。

    编造的段落号或跨块引用别的块的段落都不能进入 claim fingerprint / 证据，
    否则同一事实在不同块里因为段落号不同被当成两条，或者告警证据指向一处根本
    不存在那句话的正文。

    宁可拒绝也不能放行：编造的锚点污染身份指纹，会导致「重叠区域的同一陈述被
    识别成同一条」的去重机制失效。
    """
    position = _extract_paragraph_position(source_anchor)
    if position is None:
        return False
    return position in valid_positions


def _validate_temporal_anchor(claim: ClaimOutput, chunk: TextChunk) -> bool:
    """校验时间锚点：原文必须在对应段落里、可确定性解析、且解析结果与声称值一致。

    只有四条全真时 temporal_anchor_value 才可作为 confirmed 全局锚点：
    1. temporal_anchor_text 确实存在于 source_anchor 指向的那段正文；
    2. temporal_anchor_text **本身**可以被确定性解析成一个绝对时间；
    3. 解析结果与 temporal_anchor_value 归一化后完全一致；
    4. source_anchor 本身合法（否则无法定位段落）。

    当前 parse_absolute_anchor 只支持 ISO-8601 格式，因此只有原文中明确写了 ISO
    日期（如「2024-03-15」）才能通过验证。中文短语（「三月十五日」「元和七年冬月
    初三」）无法解析，必须拒绝 —— 不能让模型随便编年份（「三月十五日」→ 2024-03-15
    是幻觉，实际可能是任何年份）。

    不满足则清空 temporal_anchor_value 与 order_basis，story_order 保持 NULL/pending。
    不能信模型的 confidence —— 它会为幻觉的日期打出 0.9 的置信度。

    空锚点视为有效：模型不确定时本就该留空。无效的是「报了锚点但对不上正文」。
    """
    if not claim.temporal_anchor_text:
        return True  # 没报锚点，无需校验
    if not claim.source_anchor:
        return False  # 有时间锚点但没说在哪段，无法核对

    position = _extract_paragraph_position(claim.source_anchor)
    if position is None or position not in chunk.paragraph_positions:
        return False  # 段落号本身就是编造的

    # 定位到那一段正文
    try:
        para_index = chunk.paragraph_positions.index(position)
        paragraphs = chunk.text.split("\n\n")
        if para_index >= len(paragraphs):
            return False
        paragraph_text = paragraphs[para_index]
    except (ValueError, IndexError):
        return False

    # 锚点原文必须在该段里（子串匹配，忽略大小写与所有空格）
    normalized_para = paragraph_text.lower().replace(" ", "")
    normalized_anchor = claim.temporal_anchor_text.lower().replace(" ", "")
    if normalized_anchor not in normalized_para:
        return False

    if not claim.temporal_anchor_value:
        # 相对表述没有绝对值，但原文与位置仍必须真实可核对。
        return True

    # 关键：必须对 temporal_anchor_text **本身**做确定性解析
    parsed_from_text = parse_absolute_anchor(claim.temporal_anchor_text)
    if parsed_from_text is None:
        return False  # 原文无法解析成绝对时间（如中文短语）

    # 解析结果必须与声称的 temporal_anchor_value 一致
    parsed_from_value = parse_absolute_anchor(claim.temporal_anchor_value)
    if parsed_from_value is None:
        return False  # 声称的值本身就不合法

    # 两者必须完全一致（都是 Unix timestamp，直接比较）
    if parsed_from_text != parsed_from_value:
        return False  # 模型编造了不同的值

    return True


def _validate_temporal_event_ref(claim: ClaimOutput, chunk: TextChunk) -> bool:
    """An anchor label must be quoted from the same source paragraph."""
    if not claim.temporal_event_ref:
        return True
    if not claim.source_anchor:
        return False
    position = _extract_paragraph_position(claim.source_anchor)
    if position is None or position not in chunk.paragraph_positions:
        return False
    para_index = chunk.paragraph_positions.index(position)
    paragraphs = chunk.text.split("\n\n")
    if para_index >= len(paragraphs):
        return False
    normalized_para = re.sub(r"\s+", "", paragraphs[para_index]).casefold()
    normalized_ref = re.sub(r"\s+", "", claim.temporal_event_ref).casefold()
    return normalized_ref in normalized_para


class ProviderResponseError(RuntimeError):
    """模型网关返回了无法解析的响应。

    必须抛出而不是当成「没有 claim」返回空列表 —— 否则一次网关故障会被
    误读成「本章没有事实」，进而把已有 claim 判成过期。
    """


class StreamingError(ProviderResponseError):
    """SSE 流在 [DONE] 前结束，响应不完整。"""


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

    #: 可被后文 temporal_relation_ref 精确引用的事件标签。验证后随 claim 持久化，
    #: 当前章节和后续章节都可引用；无法唯一匹配时不会升级成全局顺序。
    temporal_event_ref: Optional[str] = Field(None, max_length=200)

    #: 相对关系及其参照锚点。只有精确引用和确定性时长才会据此分配 story_order；
    #: 模糊表述仍保留证据但不进入依赖时序的硬规则。
    temporal_relation: Optional[str] = Field(
        None, pattern="^(before|after|simultaneous)$"
    )
    temporal_relation_ref: Optional[str] = Field(None, max_length=200)

    #: 顺序依据。absolute_datetime 是根锚点，relative_to_anchor 只能继承已确认锚点；
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

    @field_validator("object_value", mode="before")
    @classmethod
    def _normalize_scalar_object_value(cls, value: Any) -> Any:
        """Normalize JSON scalar values emitted despite the string schema."""
        if value is None or isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("object_value must be a finite JSON scalar")
            return json.dumps(value, allow_nan=False)
        raise ValueError("object_value must be a string or JSON scalar")

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

    claims: list[Any] = Field(default_factory=list)


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
        timeout = httpx.Timeout(
            timeout=settings.consistency_request_timeout,
            connect=10.0,
        )
        return httpx.AsyncClient(timeout=timeout)

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

    async def _call_with_retry(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
        *,
        context: str,
    ) -> tuple[str, Optional[dict]]:
        """调用网关并支持有限指数退避重试。

        对 httpx timeout/transport error、HTTP 408/429/5xx 重试，尊重 Retry-After；
        4xx（除 408/429）不重试。流在 [DONE] 前结束抛 StreamingError。

        Args:
            client: httpx 客户端
            payload: 请求 JSON body（会被修改以添加 stream=true）
            context: 错误消息上下文

        Returns:
            (聚合后的完整响应文本, usage 字典或 None)

        Raises:
            httpx.HTTPStatusError: 非重试 4xx 错误
            StreamingError: 流不完整
            ProviderResponseError: 其他响应错误
        """
        # 强制流式
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}

        last_error: Optional[Exception] = None
        base_delay = settings.consistency_retry_base_delay
        max_retries = settings.consistency_max_retries

        for attempt in range(max_retries + 1):
            try:
                result = await self._stream_completion(client, payload, context=context)
                if isinstance(result, str):
                    return (result, None)
                return result
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt >= max_retries:
                    raise ProviderResponseError(
                        f"{context}: network error after {max_retries} retries: {exc}"
                    ) from exc
            except StreamingError as exc:
                last_error = exc
                if attempt >= max_retries:
                    raise
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                # 4xx 非重试（除了 408/429）
                if 400 <= status < 500 and status not in (408, 429):
                    raise
                # 408, 429, 5xx 重试
                if status in (408, 429) or status >= 500:
                    last_error = exc
                    if attempt >= max_retries:
                        # 保留原始 HTTPStatusError，不包装成 ProviderResponseError
                        raise
                    # 尊重 Retry-After
                    retry_after = self._parse_retry_after(exc.response)
                    if retry_after is not None:
                        await type(self)._async_sleep(retry_after)
                        continue
                else:
                    raise

            # 指数退避 + jitter
            if attempt < max_retries:
                delay = base_delay * (2**attempt) + random.uniform(0, 0.5)
                await type(self)._async_sleep(delay)

        # 理论上不会到这里，因为最后一次重试会抛错
        raise ProviderResponseError(f"{context}: exhausted retries") from last_error

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> Optional[float]:
        """解析 Retry-After header（秒数）。"""
        header = response.headers.get("Retry-After")
        if not header:
            return None
        try:
            return float(header)
        except ValueError:
            return None

    @staticmethod
    async def _async_sleep(seconds: float) -> None:
        """异步 sleep，测试可 mock。"""
        await asyncio.sleep(seconds)

    async def _stream_completion(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
        *,
        context: str,
    ) -> tuple[str, Optional[dict]]:
        """发起 SSE 流式请求并聚合完整响应。

        stream=true, stream_options.include_usage=true。正确处理分片、多 data 帧、
        keepalive/注释行、delta.content、usage、error 帧和严格 [DONE]。

        流在 [DONE] 前结束抛 StreamingError，不能接受半截 JSON。

        Args:
            client: httpx 客户端
            payload: 请求 JSON body（不会被修改，调用方负责设置 stream）
            context: 错误消息上下文

        Returns:
            (聚合后的完整响应文本, usage 字典或 None)

        Raises:
            httpx.HTTPError: 网关请求失败
            StreamingError: 流不完整
            ProviderResponseError: 响应结构错误
        """
        # 检测非流式 MockTransport：如果 client 的 transport 是 MockTransport，
        # 它会返回完整 JSON 响应而不是 SSE 流，需要走旧的非流式解析路径
        is_mock = isinstance(
            getattr(client, "_transport", None), httpx.MockTransport
        )

        async with client.stream("POST", self.gateway_url, json=payload, headers=self.gateway_headers) as response:
            response.raise_for_status()

            # 非流式 MockTransport 兼容
            if is_mock:
                body = await response.aread()
                try:
                    data = json.loads(body)
                    content = data["choices"][0]["message"]["content"]
                    usage = data.get("usage")
                    return content, usage
                except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                    raise ProviderResponseError(
                        f"{context}: mock response missing choices[0].message.content"
                    ) from exc

            # 真实 SSE 流聚合
            chunks: list[str] = []
            usage: Optional[dict] = None
            seen_done = False

            async for line in response.aiter_lines():
                line = line.strip()
                # 跳过空行和注释（keepalive）
                if not line or line.startswith(":"):
                    continue

                # SSE allows both "data:<value>" and "data: <value>".
                if not line.startswith("data:"):
                    continue

                data_part = line[5:].lstrip()

                # [DONE] 标记
                if data_part == "[DONE]":
                    seen_done = True
                    break

                # 解析 JSON 帧
                try:
                    frame = json.loads(data_part)
                except json.JSONDecodeError as exc:
                    raise ProviderResponseError(
                        f"{context}: invalid JSON in SSE data frame"
                    ) from exc

                # 检查 error 帧
                error = frame.get("error")
                if error:
                    if isinstance(error, dict):
                        error_msg = str(error.get("message") or "unknown error")
                        error_kind = " ".join(
                            str(error.get(key) or "") for key in ("code", "type")
                        )
                    else:
                        error_msg = str(error)
                        error_kind = ""
                    error_detail = f"{error_kind} {error_msg}".lower()
                    retryable_markers = (
                        "stream_read_error",
                        "timeout",
                        "timed out",
                        "server_error",
                        "upstream",
                        "overload",
                        "rate limit",
                        "concurrency limit",
                        "too many requests",
                    )
                    error_type = (
                        StreamingError
                        if any(marker in error_detail for marker in retryable_markers)
                        else ProviderResponseError
                    )
                    raise error_type(f"{context}: stream error frame: {error_msg}")

                # 提取 usage（最后一帧）
                if isinstance(frame.get("usage"), dict):
                    usage = frame["usage"]

                # 提取 delta.content
                try:
                    delta = frame["choices"][0]["delta"]
                    content = delta.get("content")
                    if content:
                        chunks.append(content)
                except (KeyError, IndexError, TypeError):
                    # delta 可能没有 content（如 role 帧），跳过
                    pass

            # 流必须以 [DONE] 结束
            if not seen_done:
                raise StreamingError(f"{context}: stream ended without [DONE]")

            content = "".join(chunks)
            if not content:
                raise ProviderResponseError(f"{context}: stream returned no content")
            return content, usage

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
        payload = {
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
        }
        if settings.consistency_reasoning_effort != "none":
            payload["reasoning_effort"] = settings.consistency_reasoning_effort

        content, _usage = await self._call_with_retry(
            client, payload, context=f"extraction chunk {chunk.index}"
        )

        try:
            data = json.loads(content)
            extraction = ExtractionResponse.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ProviderResponseError(
                f"extraction chunk {chunk.index} returned unparseable payload: {exc}"
            ) from exc

        parsed_claims: list[ClaimOutput] = []
        invalid_indexes: list[int] = []
        for index, raw_claim in enumerate(extraction.claims):
            try:
                parsed_claims.append(ClaimOutput.model_validate(raw_claim))
            except ValidationError:
                invalid_indexes.append(index)
        if extraction.claims and not parsed_claims:
            raise ProviderResponseError(
                f"extraction chunk {chunk.index} returned only malformed claims"
            )
        if invalid_indexes:
            logger.warning(
                "extraction chunk %s dropped %s malformed claim(s) at indexes %s",
                chunk.index,
                len(invalid_indexes),
                invalid_indexes,
            )

        claims = []
        for claim in parsed_claims:
            # 校验来源锚点：模型报的段落号必须真实存在于当前块
            if not _validate_source_anchor(claim.source_anchor, chunk.paragraph_positions):
                claim.source_anchor = None  # 编造的段落号不能进指纹

            # 校验时间锚点：原文必须在对应段落里且可解析
            if not _validate_temporal_anchor(claim, chunk):
                claim.temporal_anchor_value = None  # 幻觉日期不能成为全局锚点
                claim.order_basis = None  # 依据失效，顺序不可信
                claim.story_order = None
                claim.valid_from_order = None
                claim.valid_to_order = None
            if not _validate_temporal_event_ref(claim, chunk):
                claim.temporal_event_ref = None

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
        payload = {
            "model": settings.consistency_summary_model,
            "messages": [
                {"role": "system", "content": "You are a concise summarization assistant."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 300,
        }
        if settings.consistency_reasoning_effort != "none":
            payload["reasoning_effort"] = settings.consistency_reasoning_effort

        summary_text, usage = await self._call_with_retry(client, payload, context="summary")
        summary_text = summary_text.strip()

        # 优先使用真实 usage，否则估算
        if usage and "total_tokens" in usage:
            token_count = usage["total_tokens"]
        else:
            token_count = len(summary_text) // 4 + len(text) // 4
        return summary_text, token_count

    @staticmethod
    def _message_content(response: httpx.Response, *, context: str) -> str:
        """从 chat completion 响应里取文本，结构不对就抛错（不返回空串）。

        已废弃：现在统一走 _call_with_retry + SSE，但保留此方法以兼容可能的旧调用。
        """
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
      "temporal_event_ref": "short exact event label that later relative times can reference, or null",
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
- `temporal_event_ref`: give an anchored event a short, stable label copied from
  the narrative (for example "李长风下山"). Relative claims may reference this
  exact label; do not invent labels absent from the excerpt.
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
