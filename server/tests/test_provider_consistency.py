"""
一致性 provider 的行为测试：长文本全量覆盖（不截断）、模型/网关可配置、
响应异常必须抛错而不是静默返回空。
"""
import json

import httpx
import pytest

from config import settings
from providers.consistency import (
    ConsistencyProvider,
    ProviderResponseError,
    claim_fingerprint,
)


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch):
    """把分块参数调小，方便在测试里制造多块。"""
    monkeypatch.setattr(settings, "consistency_chunk_chars", 500, raising=False)
    monkeypatch.setattr(settings, "consistency_chunk_overlap_chars", 50, raising=False)
    monkeypatch.setattr(settings, "consistency_max_chunks", 40, raising=False)


class RecordingGateway:
    """记录所有请求的假网关，可按调用序返回不同 payload。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.bodies.append(json.loads(request.content))
        index = min(len(self.requests) - 1, len(self._responses) - 1)
        return self._responses[index]

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    @property
    def prompts(self) -> list[str]:
        return [body["messages"][-1]["content"] for body in self.bodies]

    @property
    def models(self) -> list[str]:
        return [body["model"] for body in self.bodies]


def claims_response(*claims) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps({"claims": list(claims)})}}],
            "usage": {"total_tokens": 100},
        },
    )


def summary_response(text: str, tokens: int = 42) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": text}}],
            "usage": {"total_tokens": tokens},
        },
    )


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


def long_html(paragraph_count: int = 40, filler: int = 200) -> str:
    return "".join(f"<p>段落{i:03d}" + "文" * filler + "</p>" for i in range(paragraph_count))


async def test_long_chapter_is_split_into_multiple_gateway_calls():
    """长章节必须分多次调用，而不是截断成一次。"""
    gateway = RecordingGateway([claims_response()])
    provider = ConsistencyProvider(client=gateway.client())

    await provider.extract_claims(long_html(), "proj_a", "ch_a")

    assert len(gateway.requests) > 1


async def test_tail_of_long_chapter_reaches_the_model():
    """回归 [:8000] 截断：末段内容必须出现在某次请求的 prompt 里。"""
    gateway = RecordingGateway([claims_response()])
    provider = ConsistencyProvider(client=gateway.client())

    await provider.extract_claims(long_html(paragraph_count=60), "proj_a", "ch_a")

    combined = "\n".join(gateway.prompts)
    assert "段落000" in combined
    assert "段落059" in combined, "chapter tail was never sent to the model"
    for index in range(60):
        assert f"段落{index:03d}" in combined


async def test_claims_from_all_chunks_are_aggregated():
    """各块抽到的 claim 全部聚合返回。"""
    responses = [
        claims_response(make_claim_payload("角色A")),
        claims_response(make_claim_payload("角色B")),
        claims_response(make_claim_payload("角色C")),
    ]
    gateway = RecordingGateway(responses)
    provider = ConsistencyProvider(client=gateway.client())

    claims = await provider.extract_claims(long_html(paragraph_count=12), "proj_a", "ch_a")

    subjects = {claim["subject_text"] for claim in claims}
    assert {"角色A", "角色B"}.issubset(subjects)


async def test_duplicate_claims_across_chunks_are_deduplicated_by_fingerprint():
    """重叠区域重复抽到同一事实时只保留一条，并保留较高置信度。"""
    responses = [
        claims_response(make_claim_payload("角色A", confidence=0.6)),
        claims_response(make_claim_payload("角色A", confidence=0.95)),
    ]
    gateway = RecordingGateway(responses)
    provider = ConsistencyProvider(client=gateway.client())

    claims = await provider.extract_claims(long_html(paragraph_count=8), "proj_a", "ch_a")

    matching = [claim for claim in claims if claim["subject_text"] == "角色A"]
    assert len(matching) == 1
    assert matching[0]["confidence"] == 0.95


async def test_fingerprint_is_stable_and_case_insensitive():
    first = claim_fingerprint(
        subject_text=" 角色A ",
        predicate="Alive",
        object_type="scalar",
        object_value="TRUE",
        polarity="positive",
    )
    second = claim_fingerprint(
        subject_text="角色A",
        predicate="alive",
        object_type="scalar",
        object_value="true",
        polarity="positive",
    )
    different = claim_fingerprint(
        subject_text="角色A",
        predicate="alive",
        object_type="scalar",
        object_value="false",
        polarity="positive",
    )

    assert first == second
    assert first != different


async def test_extraction_uses_configured_model_not_hardcoded(monkeypatch):
    """模型 id 来自配置，不是硬编码的 gpt-4o-mini。"""
    monkeypatch.setattr(settings, "consistency_extraction_model", "custom-extract-model")
    gateway = RecordingGateway([claims_response()])
    provider = ConsistencyProvider(client=gateway.client())

    await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")

    assert gateway.models == ["custom-extract-model"]


async def test_summary_uses_configured_model_not_hardcoded(monkeypatch):
    monkeypatch.setattr(settings, "consistency_summary_model", "custom-summary-model")
    gateway = RecordingGateway([summary_response("摘要")])
    provider = ConsistencyProvider(client=gateway.client())

    await provider.generate_summary("<p>短文</p>")

    assert gateway.models == ["custom-summary-model"]


async def test_gateway_url_and_key_follow_configured_tier(monkeypatch):
    """网关 URL/key 按 tier 解析，不是固定读 main。"""
    monkeypatch.setattr(settings, "model_gateway_cheap_url", "http://cheap.invalid/v1/chat")
    monkeypatch.setattr(settings, "model_gateway_cheap_key", "cheap-key")
    gateway = RecordingGateway([claims_response()])
    provider = ConsistencyProvider(client=gateway.client(), gateway_tier="cheap")

    await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")

    request = gateway.requests[0]
    assert str(request.url) == "http://cheap.invalid/v1/chat"
    assert request.headers["Authorization"] == "Bearer cheap-key"


async def test_default_tier_comes_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "consistency_gateway_tier", "premium")
    monkeypatch.setattr(settings, "model_gateway_premium_url", "http://premium.invalid/v1/chat")
    monkeypatch.setattr(settings, "model_gateway_premium_key", "premium-key")
    gateway = RecordingGateway([claims_response()])
    provider = ConsistencyProvider(client=gateway.client())

    await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")

    assert str(gateway.requests[0].url) == "http://premium.invalid/v1/chat"
    assert gateway.requests[0].headers["Authorization"] == "Bearer premium-key"


async def test_unknown_tier_is_rejected():
    with pytest.raises(ValueError, match="unknown model gateway tier"):
        settings.gateway_url("mythical")


async def test_gateway_http_error_propagates():
    """网关 5xx 必须抛出，让上游把 run 标记为失败。"""
    gateway = RecordingGateway([httpx.Response(503, json={"error": "unavailable"})])
    provider = ConsistencyProvider(client=gateway.client())

    with pytest.raises(httpx.HTTPStatusError):
        await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")


async def test_malformed_json_payload_raises_instead_of_returning_empty():
    """网关返回非 JSON 内容时抛错，不能被当成「本章没有事实」。"""
    gateway = RecordingGateway(
        [httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})]
    )
    provider = ConsistencyProvider(client=gateway.client())

    with pytest.raises(ProviderResponseError):
        await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")


async def test_missing_choices_raises():
    gateway = RecordingGateway([httpx.Response(200, json={"unexpected": True})])
    provider = ConsistencyProvider(client=gateway.client())

    with pytest.raises(ProviderResponseError, match="missing choices"):
        await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")


async def test_claim_failing_schema_validation_raises():
    """object_type 非法时抛错，避免写入违反 CHECK 约束的数据。"""
    gateway = RecordingGateway([claims_response(make_claim_payload("角色A", object_type="bogus"))])
    provider = ConsistencyProvider(client=gateway.client())

    with pytest.raises(ProviderResponseError):
        await provider.extract_claims("<p>短文</p>", "proj_a", "ch_a")


async def test_empty_content_makes_no_gateway_call():
    gateway = RecordingGateway([claims_response()])
    provider = ConsistencyProvider(client=gateway.client())

    assert await provider.extract_claims("", "proj_a", "ch_a") == []
    assert await provider.generate_summary("") == ("", 0)
    assert gateway.requests == []


async def test_long_content_summary_is_map_reduced_over_all_chunks():
    """长内容摘要：每块都摘要一次，再合并成总摘要（尾部不丢）。"""
    gateway = RecordingGateway([summary_response("部分摘要")])
    provider = ConsistencyProvider(client=gateway.client())

    summary, tokens = await provider.generate_summary(long_html(paragraph_count=20))

    chunk_count = len(provider.split_content(long_html(paragraph_count=20)))
    assert chunk_count > 1
    # 每块一次 + 一次合并
    assert len(gateway.requests) == chunk_count + 1
    assert summary == "部分摘要"
    assert tokens > 0
    combined_prompts = "\n".join(gateway.prompts[:chunk_count])
    assert "段落019" in combined_prompts


async def test_short_content_summary_is_single_call():
    gateway = RecordingGateway([summary_response("一句话摘要", tokens=17)])
    provider = ConsistencyProvider(client=gateway.client())

    summary, tokens = await provider.generate_summary("<p>短文</p>")

    assert len(gateway.requests) == 1
    assert summary == "一句话摘要"
    assert tokens == 17


async def test_chapter_exceeding_max_chunks_raises_rather_than_truncating(monkeypatch):
    """超出块数上限时显式失败，绝不悄悄丢掉后半章。"""
    monkeypatch.setattr(settings, "consistency_max_chunks", 2)
    gateway = RecordingGateway([claims_response()])
    provider = ConsistencyProvider(client=gateway.client())

    with pytest.raises(ValueError, match="max_chunks"):
        await provider.extract_claims(long_html(paragraph_count=40), "proj_a", "ch_a")
