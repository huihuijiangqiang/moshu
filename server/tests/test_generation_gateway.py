import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from services.generation import (
    GenerationGateway,
    GenerationProviderError,
    GenerationRoute,
    retryable_stream,
)


def package():
    return SimpleNamespace(
        route=GenerationRoute(
            source="platform",
            endpoint="https://gateway.invalid/v1/chat/completions",
            model_id="test-model",
            model_tier="main",
            api_key="test-key",
        ),
        model_id="test-model",
        model_tier="main",
        messages=[{"role": "user", "content": "continue"}],
        target_words=1000,
    )


@pytest.mark.asyncio
async def test_http_error_body_is_read_before_stream_context_closes():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            530,
            request=request,
            json={"error": {"message": "upstream timeout"}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(
            GenerationProviderError,
            match=r"HTTP 530: upstream timeout",
        ):
            _ = [event async for event in GenerationGateway(client).stream(package())]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_html_error_page_is_not_reflected_to_the_browser():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            530,
            request=request,
            headers={"content-type": "text/html"},
            text="<!doctype html><title>private upstream diagnostics</title>",
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(GenerationProviderError) as captured:
            _ = [event async for event in GenerationGateway(client).stream(package())]
    finally:
        await client.aclose()

    assert str(captured.value) == "模型网关返回 HTTP 530: 上游服务暂时不可用"
    assert "private upstream diagnostics" not in str(captured.value)


@pytest.mark.asyncio
async def test_successful_sse_stream_still_yields_text_and_usage():
    frames = [
        {"choices": [{"delta": {"content": "第一段"}}]},
        {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 3}},
    ]
    body = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames)
    body += "data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://gateway.invalid/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, request=request, text=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        events = [event async for event in GenerationGateway(client).stream(package())]
    finally:
        await client.aclose()

    assert [(event.type, event.text) for event in events] == [
        ("chunk", "第一段"),
        ("usage", ""),
    ]
    assert events[-1].usage == {"prompt_tokens": 10, "completion_tokens": 3}


@pytest.mark.asyncio
async def test_incomplete_sse_stream_has_recoverable_error_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            text='data: {"choices":[{"delta":{"content":"半截"}}]}\n\n',
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(GenerationProviderError) as captured:
            _ = [event async for event in GenerationGateway(client).stream(package())]
    finally:
        await client.aclose()

    assert captured.value.code == "STREAM_INTERRUPTED"


@pytest.mark.asyncio
async def test_http_error_is_retried_only_before_visible_output(monkeypatch):
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(530, request=request, text="temporary tunnel error")

    sleep = AsyncMock()
    monkeypatch.setattr("services.generation.asyncio.sleep", sleep)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(GenerationProviderError, match="HTTP 530"):
            _ = [
                event
                async for event in retryable_stream(
                    GenerationGateway(client), package(), retries=2
                )
            ]
    finally:
        await client.aclose()

    assert attempts == 3
    assert sleep.await_count == 2
