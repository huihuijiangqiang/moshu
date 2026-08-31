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


# --- 叙事顺序字段（架构 4.3） -------------------------------------------------
#
# 这组测试盯住的缺陷是：ClaimOutput 和抽取提示词里都没有 timeline_id /
# story_order / valid_from_order / valid_to_order，模型根本没有渠道输出这些字段。
# 结果管道只能自己「推导」顺序 —— 而任何推导（尤其用章节序号顶替）都会把正常的
# 倒叙判成时序矛盾。字段必须可空、必须在提示词里说清「不确定就留 null」。


async def test_extraction_prompt_asks_for_the_order_fields():
    """提示词必须把四个时间线字段都列进 JSON 结构，否则模型不会输出它们。"""
    gateway = RecordingGateway([claims_response()])
    provider = ConsistencyProvider(client=gateway.client())

    await provider.extract_claims("<p>短正文</p>", "proj_a", "ch_a")

    prompt = gateway.prompts[0]
    for field in ("timeline_id", "story_order", "valid_from_order", "valid_to_order"):
        assert field in prompt, f"提示词缺少 {field}，模型无法输出这个字段"


async def test_extraction_prompt_states_story_order_is_not_the_chapter_number():
    """提示词必须明确 story_order 是故事世界顺序，不是章节号，并要求倒叙取更小值。"""
    gateway = RecordingGateway([claims_response()])
    provider = ConsistencyProvider(client=gateway.client())

    await provider.extract_claims("<p>短正文</p>", "proj_a", "ch_a")

    prompt = gateway.prompts[0]
    assert "STORY WORLD" in prompt
    assert "NOT the chapter number" in prompt
    assert "flashback" in prompt.lower()
    assert "SMALLER story_order" in prompt


async def test_extraction_prompt_forbids_guessing_the_order():
    """提示词必须要求「判断不了就留 null」—— 模型天然倾向把字段填满。"""
    gateway = RecordingGateway([claims_response()])
    provider = ConsistencyProvider(client=gateway.client())

    await provider.extract_claims("<p>短正文</p>", "proj_a", "ch_a")

    prompt = gateway.prompts[0]
    assert "DO NOT GUESS" in prompt
    assert "order_basis" in prompt
    assert "order_confidence" in prompt
    assert "unknown" in prompt


async def test_order_fields_round_trip_through_parsing():
    """模型给出的顺序字段必须原样出现在解析结果里，不被丢掉也不被改写。"""
    gateway = RecordingGateway([
        claims_response(
            make_claim_payload(
                "李长风",
                timeline_id="thread_a",
                story_order=12.5,
                valid_from_order=12.5,
                valid_to_order=30.0,
                order_basis="explicit_time",
                order_confidence=0.9,
            )
        )
    ])
    provider = ConsistencyProvider(client=gateway.client())

    claims = await provider.extract_claims("<p>短正文</p>", "proj_a", "ch_a")

    claim = claims[0]
    assert claim["timeline_id"] == "thread_a"
    assert claim["story_order"] == 12.5
    assert claim["valid_from_order"] == 12.5
    assert claim["valid_to_order"] == 30.0
    assert claim["order_basis"] == "explicit_time"
    assert claim["order_confidence"] == 0.9


async def test_order_fields_default_to_null_when_the_model_omits_them():
    """字段全部可空：保守模型不给这些字段时不能报错，也不能编默认值。"""
    gateway = RecordingGateway([claims_response(make_claim_payload("李长风"))])
    provider = ConsistencyProvider(client=gateway.client())

    claims = await provider.extract_claims("<p>短正文</p>", "proj_a", "ch_a")

    claim = claims[0]
    for field in (
        "timeline_id",
        "story_order",
        "valid_from_order",
        "valid_to_order",
        "order_basis",
        "order_confidence",
    ):
        assert claim[field] is None, f"{field} 被填了默认值 {claim[field]!r}"


async def test_unknown_order_basis_clears_the_order_values():
    """order_basis=unknown 时顺序值被清空 —— 不让不可信的顺序流进硬规则。

    模型常见的行为是「说自己不知道，同时还是填了个数」。这种数值必须在解析阶段
    就丢掉，否则下游看到非空 story_order 就会拿它去排序。
    """
    gateway = RecordingGateway([
        claims_response(
            make_claim_payload(
                "李长风",
                timeline_id="main",
                story_order=99.0,
                valid_from_order=99.0,
                valid_to_order=120.0,
                order_basis="unknown",
                order_confidence=0.1,
            )
        )
    ])
    provider = ConsistencyProvider(client=gateway.client())

    claim = (await provider.extract_claims("<p>短正文</p>", "proj_a", "ch_a"))[0]

    assert claim["story_order"] is None
    assert claim["valid_from_order"] is None
    assert claim["valid_to_order"] is None
    assert claim["order_basis"] == "unknown", "依据本身保留，供观测抽取质量"


async def test_inverted_interval_is_rejected():
    """valid_to_order <= valid_from_order 说明模型没算清楚，整段区间都不可信。"""
    gateway = RecordingGateway([
        claims_response(
            make_claim_payload(
                "李长风",
                story_order=10.0,
                valid_from_order=30.0,
                valid_to_order=10.0,
                order_basis="explicit_time",
                order_confidence=0.9,
            )
        )
    ])
    provider = ConsistencyProvider(client=gateway.client())

    claim = (await provider.extract_claims("<p>短正文</p>", "proj_a", "ch_a"))[0]

    assert claim["valid_from_order"] is None
    assert claim["valid_to_order"] is None


async def test_invalid_order_basis_is_rejected():
    """order_basis 只接受四个约定值，拼错必须报错而不是静默当成可信依据。"""
    gateway = RecordingGateway([
        claims_response(make_claim_payload("李长风", order_basis="i_guessed"))
    ])
    provider = ConsistencyProvider(client=gateway.client())

    with pytest.raises(ProviderResponseError):
        await provider.extract_claims("<p>短正文</p>", "proj_a", "ch_a")


async def test_order_fields_do_not_change_the_fingerprint():
    """指纹只由语义构成：同一事实在不同顺序判断下必须是同一行。

    否则模型某次把 order_confidence 从 0.9 改成 0.85，指纹就变了，同一事实会
    在同一版本里插出两行，作者的处置记录也会失效。
    """
    gateway = RecordingGateway([
        claims_response(
            make_claim_payload("李长风", story_order=1.0, order_basis="explicit_time",
                               order_confidence=0.9),
        ),
        claims_response(
            make_claim_payload("李长风", story_order=77.0, order_basis="flashback",
                               order_confidence=0.2),
        ),
    ])
    provider = ConsistencyProvider(client=gateway.client())

    claims = await provider.extract_claims(long_html(paragraph_count=8), "proj_a", "ch_a")

    matching = [c for c in claims if c["subject_text"] == "李长风"]
    assert len(matching) == 1, "顺序字段参与了指纹，同一事实被拆成多行"
