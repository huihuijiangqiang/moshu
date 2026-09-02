"""
SSE 流式与重试测试：分片 JSON、多帧、keepalive、缺 [DONE]、error 帧、
timeout 重试成功/耗尽、非重试 4xx。

所有测试无网络，不实际 sleep。
"""
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import ValidationError

from config import settings
from providers.consistency import (
    ClaimOutput,
    ConsistencyProvider,
    ProviderResponseError,
    StreamingError,
)


def claim_model(**overrides):
    payload = make_claim_payload("角色A")
    payload.update(overrides)
    return ClaimOutput.model_validate(payload)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, "true"),
        (False, "false"),
        (7, "7"),
        (1.25, "1.25"),
        (None, None),
        ("原值", "原值"),
    ],
)
def test_object_value_normalizes_json_scalars(raw, expected):
    assert claim_model(object_value=raw).object_value == expected


@pytest.mark.parametrize("raw", [{"nested": True}, ["nested"], float("nan")])
def test_object_value_rejects_structures_and_non_finite_numbers(raw):
    with pytest.raises(ValidationError):
        claim_model(object_value=raw)


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """所有测试不实际 sleep。"""
    monkeypatch.setattr(ConsistencyProvider, "_async_sleep", AsyncMock())


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch):
    """把分块参数调小，方便在测试里制造多块。"""
    monkeypatch.setattr(settings, "consistency_chunk_chars", 500, raising=False)
    monkeypatch.setattr(settings, "consistency_chunk_overlap_chars", 50, raising=False)
    monkeypatch.setattr(settings, "consistency_max_chunks", 40, raising=False)


def sse_frame(content_chunk: str) -> str:
    """构造一个 SSE data 帧。"""
    frame = {
        "choices": [{"delta": {"content": content_chunk}}],
    }
    return f"data: {json.dumps(frame, ensure_ascii=False)}\n"


def sse_done() -> str:
    """构造 [DONE] 标记。"""
    return "data: [DONE]\n"


def sse_error(message: str) -> str:
    """构造 error 帧。"""
    frame = {"error": {"message": message}}
    return f"data: {json.dumps(frame)}\n"


def make_claim_payload(subject: str, **overrides) -> dict:
    payload = {
        "subject_text": subject,
        "predicate": "alive",
        "object_type": "scalar",
        "object_value": "true",
        "polarity": "positive",
        "certainty": "explicit",
        "confidence": 0.9,
    }
    payload.update(overrides)
    return payload


class ChunkedAsyncByteStream(httpx.AsyncByteStream):
    """Emit explicit network chunks, including partial UTF-8 sequences."""

    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


class StreamingMockTransport(httpx.AsyncBaseTransport):
    """支持可控网络字节分片的流式响应。"""

    def __init__(
        self,
        sse_body: str,
        status_code: int = 200,
        headers: dict | None = None,
        *,
        byte_chunks: list[bytes] | None = None,
    ):
        self.sse_body = sse_body
        self.status_code = status_code
        self.headers = headers or {}
        self.byte_chunks = byte_chunks
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        return httpx.Response(
            self.status_code,
            stream=ChunkedAsyncByteStream(
                self.byte_chunks or [self.sse_body.encode("utf-8")]
            ),
            headers=self.headers,
            request=request,
        )


async def test_sse_single_frame_extraction():
    """单帧 SSE 响应：完整 JSON 一次性返回。"""
    claims_json = json.dumps({"claims": [make_claim_payload("角色A")]})
    sse_body = sse_frame(claims_json) + sse_done()

    transport = StreamingMockTransport(sse_body)
    client = httpx.AsyncClient(transport=transport)
    provider = ConsistencyProvider(client=client)

    claims = await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")

    assert len(claims) == 1
    assert claims[0]["subject_text"] == "角色A"


async def test_sse_multiple_frames_extraction():
    """多帧 SSE 响应：JSON 分成多个 delta.content 块。"""
    claims_json = json.dumps({"claims": [make_claim_payload("角色A")]})
    # 把 JSON 切成三段
    chunk1 = claims_json[:20]
    chunk2 = claims_json[20:40]
    chunk3 = claims_json[40:]

    sse_body = sse_frame(chunk1) + sse_frame(chunk2) + sse_frame(chunk3) + sse_done()

    transport = StreamingMockTransport(sse_body)
    client = httpx.AsyncClient(transport=transport)
    provider = ConsistencyProvider(client=client)

    claims = await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")

    assert len(claims) == 1
    assert claims[0]["subject_text"] == "角色A"


async def test_one_malformed_claim_does_not_discard_valid_siblings(caplog):
    malformed = make_claim_payload("错误事实", polarity="uncertain")
    valid = make_claim_payload("有效事实")
    claims_json = json.dumps({"claims": [malformed, valid]}, ensure_ascii=False)
    provider = ConsistencyProvider(
        client=httpx.AsyncClient(
            transport=StreamingMockTransport(sse_frame(claims_json) + sse_done())
        )
    )

    claims = await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")

    assert [claim["subject_text"] for claim in claims] == ["有效事实"]
    assert "dropped 1 malformed claim" in caplog.text


async def test_all_malformed_claims_still_raise():
    malformed = make_claim_payload("错误事实", polarity="uncertain")
    claims_json = json.dumps({"claims": [malformed]}, ensure_ascii=False)
    provider = ConsistencyProvider(
        client=httpx.AsyncClient(
            transport=StreamingMockTransport(sse_frame(claims_json) + sse_done())
        )
    )

    with pytest.raises(ProviderResponseError, match="only malformed claims"):
        await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")


async def test_sse_survives_arbitrary_network_byte_boundaries():
    """字节流可在 UTF-8 字符和 JSON token 中间切开。"""
    claims_json = json.dumps(
        {"claims": [make_claim_payload("角色甲")]}, ensure_ascii=False
    )
    sse_body = sse_frame(claims_json) + sse_done()
    encoded = sse_body.encode("utf-8")
    transport = StreamingMockTransport(
        sse_body,
        byte_chunks=[encoded[index : index + 1] for index in range(len(encoded))],
    )
    client = httpx.AsyncClient(transport=transport)
    provider = ConsistencyProvider(client=client)

    claims = await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")

    assert claims[0]["subject_text"] == "角色甲"


async def test_sse_accepts_data_field_without_space():
    claims_json = json.dumps({"claims": [make_claim_payload("角色A")]})
    sse_body = (sse_frame(claims_json) + sse_done()).replace("data: ", "data:")
    provider = ConsistencyProvider(
        client=httpx.AsyncClient(transport=StreamingMockTransport(sse_body))
    )

    claims = await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")

    assert claims[0]["subject_text"] == "角色A"


async def test_sse_with_keepalive_comments():
    """SSE 流包含 keepalive 注释行（: comment）和空行，必须跳过。"""
    claims_json = json.dumps({"claims": [make_claim_payload("角色A")]})
    sse_body = (
        ": keepalive\n"
        + "\n"
        + sse_frame(claims_json[:30])
        + ": another keepalive\n"
        + "\n"
        + sse_frame(claims_json[30:])
        + "\n"
        + sse_done()
    )

    transport = StreamingMockTransport(sse_body)
    client = httpx.AsyncClient(transport=transport)
    provider = ConsistencyProvider(client=client)

    claims = await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")

    assert len(claims) == 1
    assert claims[0]["subject_text"] == "角色A"


async def test_sse_missing_done_raises_streaming_error():
    """流在 [DONE] 前结束必须抛 StreamingError，不能接受半截 JSON。"""
    claims_json = json.dumps({"claims": [make_claim_payload("角色A")]})
    # 只有一半 JSON，没有 [DONE]
    sse_body = sse_frame(claims_json[:30])

    transport = StreamingMockTransport(sse_body)
    client = httpx.AsyncClient(transport=transport)
    provider = ConsistencyProvider(client=client)

    with pytest.raises(StreamingError, match="without \\[DONE\\]"):
        await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")


async def test_sse_missing_done_is_retried(monkeypatch):
    monkeypatch.setattr(settings, "consistency_max_retries", 1, raising=False)
    claims_json = json.dumps({"claims": [make_claim_payload("角色A")]})
    transport = StreamingMockTransport(sse_frame(claims_json))
    provider = ConsistencyProvider(client=httpx.AsyncClient(transport=transport))

    with pytest.raises(StreamingError, match="without \\[DONE\\]"):
        await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")

    assert len(transport.requests) == 2


async def test_sse_invalid_json_frame_is_rejected():
    provider = ConsistencyProvider(
        client=httpx.AsyncClient(
            transport=StreamingMockTransport("data: {not-json}\n" + sse_done())
        )
    )

    with pytest.raises(ProviderResponseError, match="invalid JSON"):
        await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")


async def test_sse_error_frame_raises():
    """SSE 流包含 error 帧必须抛错。"""
    sse_body = sse_error("model overloaded") + sse_done()

    transport = StreamingMockTransport(sse_body)
    client = httpx.AsyncClient(transport=transport)
    provider = ConsistencyProvider(client=client)

    with pytest.raises(ProviderResponseError, match="stream error frame: model overloaded"):
        await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")


async def test_stream_read_error_frame_is_retried(monkeypatch):
    monkeypatch.setattr(settings, "consistency_max_retries", 1, raising=False)
    call_count = 0

    async def mock_stream_completion(self, client, payload, *, context):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise StreamingError(f"{context}: stream error frame: stream_read_error")
        return json.dumps({"claims": [make_claim_payload("角色A")]})

    with patch.object(ConsistencyProvider, "_stream_completion", mock_stream_completion):
        claims = await ConsistencyProvider().extract_claims(
            "<p>短文</p>", "proj_a", "ch_a"
        )

    assert call_count == 2
    assert claims[0]["subject_text"] == "角色A"


async def test_concurrency_limit_error_frame_is_retried(monkeypatch):
    monkeypatch.setattr(settings, "consistency_max_retries", 1, raising=False)
    transport = StreamingMockTransport(
        sse_error("Concurrency limit exceeded for user") + sse_done()
    )
    provider = ConsistencyProvider(client=httpx.AsyncClient(transport=transport))

    with pytest.raises(StreamingError, match="Concurrency limit exceeded"):
        await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")

    assert len(transport.requests) == 2


async def test_sse_role_frame_without_content_is_skipped():
    """delta 没有 content 的帧（如 role 帧）必须跳过，不能报错。"""
    claims_json = json.dumps({"claims": [make_claim_payload("角色A")]})
    role_frame = 'data: {"choices": [{"delta": {"role": "assistant"}}]}\n'
    sse_body = role_frame + sse_frame(claims_json) + sse_done()

    transport = StreamingMockTransport(sse_body)
    client = httpx.AsyncClient(transport=transport)
    provider = ConsistencyProvider(client=client)

    claims = await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")

    assert len(claims) == 1
    assert claims[0]["subject_text"] == "角色A"


async def test_timeout_retries_and_succeeds(monkeypatch):
    """httpx.TimeoutException 重试后成功。"""
    monkeypatch.setattr(settings, "consistency_max_retries", 2, raising=False)

    call_count = 0

    async def mock_stream_completion(self, client, payload, *, context):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.TimeoutException("first attempt timeout")
        # 第二次成功
        return json.dumps({"claims": [make_claim_payload("角色A")]})

    with patch.object(ConsistencyProvider, "_stream_completion", mock_stream_completion):
        provider = ConsistencyProvider()
        claims = await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")

    assert call_count == 2
    assert len(claims) == 1


async def test_timeout_exhausts_retries(monkeypatch):
    """httpx.TimeoutException 重试耗尽后抛错。"""
    monkeypatch.setattr(settings, "consistency_max_retries", 2, raising=False)

    async def mock_stream_completion(self, client, payload, *, context):
        raise httpx.TimeoutException("persistent timeout")

    with patch.object(ConsistencyProvider, "_stream_completion", mock_stream_completion):
        provider = ConsistencyProvider()
        with pytest.raises(ProviderResponseError, match="network error after 2 retries"):
            await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")


async def test_http_500_retries_and_succeeds(monkeypatch):
    """HTTP 500 重试后成功。"""
    monkeypatch.setattr(settings, "consistency_max_retries", 2, raising=False)

    call_count = 0

    async def mock_stream_completion(self, client, payload, *, context):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            response = httpx.Response(500, json={"error": "internal error"})
            raise httpx.HTTPStatusError("500", request=None, response=response)
        return json.dumps({"claims": [make_claim_payload("角色A")]})

    with patch.object(ConsistencyProvider, "_stream_completion", mock_stream_completion):
        provider = ConsistencyProvider()
        claims = await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")

    assert call_count == 2
    assert len(claims) == 1


async def test_http_503_retries_and_succeeds(monkeypatch):
    """HTTP 503 重试后成功。"""
    monkeypatch.setattr(settings, "consistency_max_retries", 1, raising=False)

    call_count = 0

    async def mock_stream_completion(self, client, payload, *, context):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            response = httpx.Response(503, json={"error": "service unavailable"})
            raise httpx.HTTPStatusError("503", request=None, response=response)
        return json.dumps({"claims": [make_claim_payload("角色A")]})

    with patch.object(ConsistencyProvider, "_stream_completion", mock_stream_completion):
        provider = ConsistencyProvider()
        await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")

    assert call_count == 2


async def test_http_429_retries_and_succeeds(monkeypatch):
    """HTTP 429 重试后成功。"""
    monkeypatch.setattr(settings, "consistency_max_retries", 1, raising=False)

    call_count = 0

    async def mock_stream_completion(self, client, payload, *, context):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            response = httpx.Response(429, json={"error": "rate limited"})
            raise httpx.HTTPStatusError("429", request=None, response=response)
        return json.dumps({"claims": [make_claim_payload("角色A")]})

    with patch.object(ConsistencyProvider, "_stream_completion", mock_stream_completion):
        provider = ConsistencyProvider()
        await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")

    assert call_count == 2


async def test_http_408_retries_and_succeeds(monkeypatch):
    """HTTP 408 重试后成功。"""
    monkeypatch.setattr(settings, "consistency_max_retries", 1, raising=False)

    call_count = 0

    async def mock_stream_completion(self, client, payload, *, context):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            request = httpx.Request("POST", "http://test")
            response = httpx.Response(408, json={"error": "request timeout"}, request=request)
            raise httpx.HTTPStatusError("408", request=request, response=response)
        return json.dumps({"claims": [make_claim_payload("角色A")]})

    with patch.object(ConsistencyProvider, "_stream_completion", mock_stream_completion):
        provider = ConsistencyProvider()
        await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")

    assert call_count == 2


async def test_http_400_does_not_retry():
    """HTTP 400 不重试，直接抛错。"""
    async def mock_stream_completion(self, client, payload, *, context):
        response = httpx.Response(400, json={"error": "bad request"})
        raise httpx.HTTPStatusError("400", request=None, response=response)

    with patch.object(ConsistencyProvider, "_stream_completion", mock_stream_completion):
        provider = ConsistencyProvider()
        with pytest.raises(httpx.HTTPStatusError, match="400"):
            await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")


async def test_http_401_does_not_retry():
    """HTTP 401 不重试，直接抛错。"""
    async def mock_stream_completion(self, client, payload, *, context):
        response = httpx.Response(401, json={"error": "unauthorized"})
        raise httpx.HTTPStatusError("401", request=None, response=response)

    with patch.object(ConsistencyProvider, "_stream_completion", mock_stream_completion):
        provider = ConsistencyProvider()
        with pytest.raises(httpx.HTTPStatusError, match="401"):
            await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")


async def test_http_404_does_not_retry():
    """HTTP 404 不重试，直接抛错。"""
    async def mock_stream_completion(self, client, payload, *, context):
        response = httpx.Response(404, json={"error": "not found"})
        raise httpx.HTTPStatusError("404", request=None, response=response)

    with patch.object(ConsistencyProvider, "_stream_completion", mock_stream_completion):
        provider = ConsistencyProvider()
        with pytest.raises(httpx.HTTPStatusError, match="404"):
            await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")


async def test_retry_after_header_is_respected(monkeypatch):
    """尊重 Retry-After header。"""
    monkeypatch.setattr(settings, "consistency_max_retries", 1, raising=False)

    call_count = 0
    sleep_duration = None

    async def mock_sleep(seconds: float):
        nonlocal sleep_duration
        sleep_duration = seconds

    monkeypatch.setattr(ConsistencyProvider, "_async_sleep", mock_sleep)

    async def mock_stream_completion(self, client, payload, *, context):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            request = httpx.Request("POST", "http://test")
            response = httpx.Response(
                429, json={"error": "rate limited"}, headers={"Retry-After": "5.0"}, request=request
            )
            raise httpx.HTTPStatusError("429", request=request, response=response)
        return json.dumps({"claims": [make_claim_payload("角色A")]})

    with patch.object(ConsistencyProvider, "_stream_completion", mock_stream_completion):
        provider = ConsistencyProvider()
        await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")

    assert sleep_duration == 5.0


async def test_transport_error_retries_and_succeeds(monkeypatch):
    """httpx.TransportError 重试后成功。"""
    monkeypatch.setattr(settings, "consistency_max_retries", 1, raising=False)

    call_count = 0

    async def mock_stream_completion(self, client, payload, *, context):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.TransportError("connection reset")
        return json.dumps({"claims": [make_claim_payload("角色A")]})

    with patch.object(ConsistencyProvider, "_stream_completion", mock_stream_completion):
        provider = ConsistencyProvider()
        claims = await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")

    assert call_count == 2
    assert len(claims) == 1


async def test_summary_uses_streaming():
    """摘要同样走 SSE 流式。"""
    summary_text = "这是一段摘要"
    sse_body = sse_frame(summary_text) + sse_done()

    transport = StreamingMockTransport(sse_body)
    client = httpx.AsyncClient(transport=transport)
    provider = ConsistencyProvider(client=client)

    summary, tokens = await provider.generate_summary("<p>短文</p>")

    assert summary == summary_text
    assert tokens > 0


async def test_reasoning_effort_is_included_when_configured(monkeypatch):
    """reasoning_effort 配置为非 none 时包含在请求中。"""
    monkeypatch.setattr(settings, "consistency_reasoning_effort", "medium", raising=False)

    sent_payload = None

    async def mock_stream_completion(self, client, payload, *, context):
        nonlocal sent_payload
        sent_payload = payload
        return json.dumps({"claims": [make_claim_payload("角色A")]})

    with patch.object(ConsistencyProvider, "_stream_completion", mock_stream_completion):
        provider = ConsistencyProvider()
        await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")

    assert sent_payload["reasoning_effort"] == "medium"


async def test_reasoning_effort_is_excluded_when_none(monkeypatch):
    """reasoning_effort 配置为 none 时不包含在请求中。"""
    monkeypatch.setattr(settings, "consistency_reasoning_effort", "none", raising=False)

    sent_payload = None

    async def mock_stream_completion(self, client, payload, *, context):
        nonlocal sent_payload
        sent_payload = payload
        return json.dumps({"claims": [make_claim_payload("角色A")]})

    with patch.object(ConsistencyProvider, "_stream_completion", mock_stream_completion):
        provider = ConsistencyProvider()
        await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")

    assert "reasoning_effort" not in sent_payload


async def test_stream_always_has_stream_true():
    """所有请求都包含 stream=true 和 stream_options。"""
    sent_payloads = []

    async def mock_stream_completion(self, client, payload, *, context):
        sent_payloads.append(payload.copy())
        return json.dumps({"claims": [make_claim_payload("角色A")]})

    with patch.object(ConsistencyProvider, "_stream_completion", mock_stream_completion):
        provider = ConsistencyProvider()
        await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")

    assert len(sent_payloads) > 0
    for payload in sent_payloads:
        assert payload["stream"] is True
        assert payload["stream_options"] == {"include_usage": True}
