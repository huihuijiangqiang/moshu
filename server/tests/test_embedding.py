"""
Embedding provider 与 codex RAG 写入路径的行为测试。

方言说明：向量列在 SQLite 上编译成 TEXT，pgvector 的 bind/result processor 仍会
把 list 序列化/反序列化，所以「写入 embedding 并读回」是可以在 SQLite 上真实验证的。
只有 `<=>` 距离运算符（L3 向量检索）SQLite 没有实现，那部分放在
tests/integration/ 的 PostgreSQL 用例里（未跑过）。
"""
import json

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from config import Settings, settings
from db.models_codex import CodexAlias, CodexEntry
from services.codex_embedding import (
    build_embedding_text,
    embed_codex_entry,
    embed_missing_codex_entries,
)
from services.embedding import EmbeddingProviderError, GatewayEmbeddingProvider
from services.providers import MockEmbeddingProvider


@pytest.fixture(autouse=True)
def small_dimensions(monkeypatch):
    """把维度调小，并隔离开发机 .env 中的真实独立网关配置。"""
    monkeypatch.setattr(settings, "embedding_dimensions", 4)
    monkeypatch.setattr(settings, "embedding_gateway_url", None)
    monkeypatch.setattr(settings, "embedding_gateway_key", None)


class RecordingEmbeddingGateway:
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


def embeddings_response(*vectors, shuffle_indexes: bool = False) -> httpx.Response:
    data = [{"index": position, "embedding": list(vector)} for position, vector in enumerate(vectors)]
    if shuffle_indexes:
        data = list(reversed(data))
    return httpx.Response(200, json={"data": data, "model": "test-embed"})


def test_settings_reject_dimension_that_does_not_match_database(monkeypatch):
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "1536")

    with pytest.raises(ValidationError, match="HALFVEC\\(2048\\)"):
        Settings(_env_file=None)


# --- GatewayEmbeddingProvider ---------------------------------------------------


async def test_embed_text_returns_vector_from_gateway():
    gateway = RecordingEmbeddingGateway([embeddings_response([0.1, 0.2, 0.3, 0.4])])
    provider = GatewayEmbeddingProvider(client=gateway.client())

    vector = await provider.embed_text("角色A")

    assert vector == [0.1, 0.2, 0.3, 0.4]
    assert gateway.bodies[0]["input"] == ["角色A"]


async def test_embedding_provider_captures_gateway_usage_for_platform_ledger():
    response = httpx.Response(
        200,
        json={
            "data": [{"index": 0, "embedding": [0.1, 0.2, 0.3, 0.4]}],
            "usage": {"prompt_tokens": 17, "total_tokens": 17},
        },
    )
    provider = GatewayEmbeddingProvider(client=RecordingEmbeddingGateway([response]).client())

    await provider.embed_text("角色A")

    assert len(provider.usage_events) == 1
    event = provider.usage_events[0]
    assert event["prompt_tokens"] == 17
    assert event["completion_tokens"] == 0
    assert event["estimated"] is False
    assert event["detail"]["input_items"] == 1


async def test_embed_uses_configured_model(monkeypatch):
    monkeypatch.setattr(settings, "embedding_model", "custom-embed-model")
    gateway = RecordingEmbeddingGateway([embeddings_response([0.0, 0.0, 0.0, 1.0])])
    provider = GatewayEmbeddingProvider(client=gateway.client())

    await provider.embed_text("角色A")

    assert gateway.bodies[0]["model"] == "custom-embed-model"


async def test_embed_endpoint_derived_from_chat_gateway_url(monkeypatch):
    monkeypatch.setattr(settings, "model_gateway_main_url", "http://gw.invalid/v1/chat/completions")
    provider = GatewayEmbeddingProvider(gateway_tier="main")

    assert provider.endpoint == "http://gw.invalid/v1/embeddings"


async def test_embed_endpoint_can_be_overridden():
    provider = GatewayEmbeddingProvider(endpoint="http://gw.invalid/custom/embed")

    assert provider.endpoint == "http://gw.invalid/custom/embed"


async def test_embed_uses_dedicated_gateway_base_url_and_credentials(monkeypatch):
    monkeypatch.setattr(settings, "embedding_gateway_url", "http://embed.invalid/api/v3/")
    monkeypatch.setattr(settings, "embedding_gateway_key", "embed-key")
    gateway = RecordingEmbeddingGateway([embeddings_response([1.0, 0.0, 0.0, 0.0])])
    provider = GatewayEmbeddingProvider(client=gateway.client())

    await provider.embed_text("角色A")

    assert str(gateway.requests[0].url) == "http://embed.invalid/api/v3/embeddings"
    assert gateway.requests[0].headers["Authorization"] == "Bearer embed-key"


async def test_dedicated_gateway_accepts_full_embeddings_endpoint(monkeypatch):
    monkeypatch.setattr(
        settings,
        "embedding_gateway_url",
        "http://embed.invalid/api/v3/embeddings",
    )
    monkeypatch.setattr(settings, "embedding_gateway_key", "embed-key")
    provider = GatewayEmbeddingProvider()

    assert provider.endpoint == "http://embed.invalid/api/v3/embeddings"


@pytest.mark.parametrize(
    ("url", "key"),
    [("http://embed.invalid/v1", None), (None, "embed-key")],
)
def test_dedicated_gateway_rejects_partial_credentials(monkeypatch, url, key):
    monkeypatch.setattr(settings, "embedding_gateway_url", url)
    monkeypatch.setattr(settings, "embedding_gateway_key", key)
    provider = GatewayEmbeddingProvider()

    with pytest.raises(EmbeddingProviderError, match="must be configured together"):
        _ = provider.endpoint


async def test_embed_uses_configured_tier_credentials(monkeypatch):
    monkeypatch.setattr(settings, "model_gateway_cheap_url", "http://cheap.invalid/v1/chat/completions")
    monkeypatch.setattr(settings, "model_gateway_cheap_key", "cheap-key")
    gateway = RecordingEmbeddingGateway([embeddings_response([1.0, 0.0, 0.0, 0.0])])
    provider = GatewayEmbeddingProvider(client=gateway.client(), gateway_tier="cheap")

    await provider.embed_text("角色A")

    assert str(gateway.requests[0].url) == "http://cheap.invalid/v1/embeddings"
    assert gateway.requests[0].headers["Authorization"] == "Bearer cheap-key"


async def test_embed_batch_preserves_input_order_even_when_gateway_reorders():
    """网关乱序返回时按 index 重排，绝不错配向量与文本。"""
    gateway = RecordingEmbeddingGateway(
        [embeddings_response([1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0], shuffle_indexes=True)]
    )
    provider = GatewayEmbeddingProvider(client=gateway.client())

    vectors = await provider.embed_batch(["a", "b", "c"])

    assert vectors == [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0]]


async def test_embed_batch_of_nothing_makes_no_request():
    gateway = RecordingEmbeddingGateway([embeddings_response([1.0, 0, 0, 0])])
    provider = GatewayEmbeddingProvider(client=gateway.client())

    assert await provider.embed_batch([]) == []
    assert gateway.requests == []


async def test_empty_input_text_is_rejected():
    provider = GatewayEmbeddingProvider(client=RecordingEmbeddingGateway([]).client())

    with pytest.raises(ValueError, match="empty text"):
        await provider.embed_text("   ")


async def test_dimension_mismatch_raises_instead_of_persisting_bad_vector():
    """维度不符必须抛错 —— 写进定长 HALFVEC 列会在 PostgreSQL 上直接报错。"""
    gateway = RecordingEmbeddingGateway([embeddings_response([0.1, 0.2])])
    provider = GatewayEmbeddingProvider(client=gateway.client())

    with pytest.raises(EmbeddingProviderError, match="dimension mismatch"):
        await provider.embed_text("角色A")


async def test_missing_data_field_raises():
    gateway = RecordingEmbeddingGateway([httpx.Response(200, json={"unexpected": 1})])
    provider = GatewayEmbeddingProvider(client=gateway.client())

    with pytest.raises(EmbeddingProviderError, match="missing 'data'"):
        await provider.embed_text("角色A")


async def test_vector_count_mismatch_raises():
    gateway = RecordingEmbeddingGateway([embeddings_response([0.1, 0.2, 0.3, 0.4])])
    provider = GatewayEmbeddingProvider(client=gateway.client())

    with pytest.raises(EmbeddingProviderError, match="1 vectors for 2 inputs"):
        await provider.embed_batch(["a", "b"])


async def test_non_numeric_vector_raises():
    gateway = RecordingEmbeddingGateway(
        [httpx.Response(200, json={"data": [{"index": 0, "embedding": ["a", "b", "c", "d"]}]})]
    )
    provider = GatewayEmbeddingProvider(client=gateway.client())

    with pytest.raises(EmbeddingProviderError, match="non-numeric"):
        await provider.embed_text("角色A")


async def test_duplicate_index_raises():
    gateway = RecordingEmbeddingGateway(
        [
            httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 0, "embedding": [1.0, 0, 0, 0]},
                        {"index": 0, "embedding": [0, 1.0, 0, 0]},
                    ]
                },
            )
        ]
    )
    provider = GatewayEmbeddingProvider(client=gateway.client())

    with pytest.raises(EmbeddingProviderError, match="duplicate index"):
        await provider.embed_batch(["a", "b"])


async def test_gateway_http_error_propagates():
    gateway = RecordingEmbeddingGateway([httpx.Response(500, json={"error": "boom"})])
    provider = GatewayEmbeddingProvider(client=gateway.client())

    with pytest.raises(httpx.HTTPStatusError):
        await provider.embed_text("角色A")


# --- MockEmbeddingProvider -----------------------------------------------------


async def test_mock_provider_produces_distinguishable_directions():
    """不同文本必须给出不同方向的向量，否则相似度检索测不出任何区分度。"""
    provider = MockEmbeddingProvider()

    first = await provider.embed_text("角色A")
    second = await provider.embed_text("完全不同的地点")

    assert first != second
    dot = sum(x * y for x, y in zip(first, second))
    assert abs(dot) < 0.999, "mock vectors are collinear; cosine distance would be meaningless"


async def test_mock_provider_is_deterministic():
    provider = MockEmbeddingProvider()

    assert await provider.embed_text("角色A") == await provider.embed_text("角色A")


async def test_mock_provider_respects_configured_dimensions():
    provider = MockEmbeddingProvider()

    assert len(await provider.embed_text("角色A")) == settings.embedding_dimensions


# --- RAG 写入路径 --------------------------------------------------------------


def make_entry(entry_id: str, project_id: str = "proj_a", **overrides) -> CodexEntry:
    fields = {
        "id": entry_id,
        "project_id": project_id,
        "kind": "character",
        "name": f"角色{entry_id}",
        "description": "描述",
        "attrs": {},
        "resident": False,
        "status": "confirmed",
        "ref_chapters": [],
        "conflicts": [],
    }
    fields.update(overrides)
    return CodexEntry(**fields)


def test_embedding_text_includes_name_kind_aliases_and_description():
    text = build_embedding_text(
        name="李长风", kind="character", description="剑修", aliases=["长风", "李剑仙"]
    )

    assert "character: 李长风" in text
    assert "长风" in text
    assert "李剑仙" in text
    assert "剑修" in text


def test_embedding_text_is_deterministic_regardless_of_alias_order():
    first = build_embedding_text(
        name="李长风", kind="character", description="剑修", aliases=["长风", "李剑仙"]
    )
    second = build_embedding_text(
        name="李长风", kind="character", description="剑修", aliases=["李剑仙", "长风", "长风"]
    )

    assert first == second


async def test_codex_entry_embedding_is_written_and_readable(
    async_db_session, seed_project
):
    """回归：以前没有任何写入路径，embedding 永远是 NULL。"""
    await seed_project()
    async_db_session.add(make_entry("cx_1"))
    await async_db_session.flush()

    entry = (
        await async_db_session.execute(select(CodexEntry).where(CodexEntry.id == "cx_1"))
    ).scalar_one()
    assert entry.embedding is None

    written = await embed_codex_entry(async_db_session, MockEmbeddingProvider(), entry)
    await async_db_session.commit()

    assert written is True
    async_db_session.expunge_all()
    reloaded = (
        await async_db_session.execute(select(CodexEntry).where(CodexEntry.id == "cx_1"))
    ).scalar_one()
    assert reloaded.embedding is not None
    assert len(list(reloaded.embedding)) == settings.embedding_dimensions


async def test_aliases_influence_the_stored_embedding(async_db_session, seed_project):
    """别名参与向量化 —— 否则用别名指代时 L3 召回不到。"""
    await seed_project()
    async_db_session.add(make_entry("cx_plain"))
    async_db_session.add(make_entry("cx_aliased"))
    await async_db_session.flush()
    async_db_session.add(CodexAlias(entry_id="cx_aliased", alias="长风"))
    await async_db_session.flush()

    provider = MockEmbeddingProvider()
    plain = (
        await async_db_session.execute(select(CodexEntry).where(CodexEntry.id == "cx_plain"))
    ).scalar_one()
    aliased = (
        await async_db_session.execute(select(CodexEntry).where(CodexEntry.id == "cx_aliased"))
    ).scalar_one()
    # 名称相同时才能证明差异来自别名
    aliased.name = plain.name
    await embed_codex_entry(async_db_session, provider, plain)
    await embed_codex_entry(async_db_session, provider, aliased)

    assert list(plain.embedding) != list(aliased.embedding)


async def test_existing_embedding_is_not_recomputed_without_force(
    async_db_session, seed_project
):
    await seed_project()
    entry = make_entry("cx_1")
    async_db_session.add(entry)
    await async_db_session.flush()
    await embed_codex_entry(async_db_session, MockEmbeddingProvider(), entry)
    original = list(entry.embedding)

    written = await embed_codex_entry(async_db_session, MockEmbeddingProvider(), entry)

    assert written is False
    assert list(entry.embedding) == original


async def test_force_recomputes_embedding_after_content_change(
    async_db_session, seed_project
):
    """描述改了要能强制重算，否则向量与内容永久不一致。"""
    await seed_project()
    entry = make_entry("cx_1")
    async_db_session.add(entry)
    await async_db_session.flush()
    await embed_codex_entry(async_db_session, MockEmbeddingProvider(), entry)
    original = list(entry.embedding)

    entry.description = "完全不同的描述，改动很大"
    written = await embed_codex_entry(async_db_session, MockEmbeddingProvider(), entry, force=True)

    assert written is True
    assert list(entry.embedding) != original


async def test_backfill_embeds_only_entries_missing_vectors(
    async_db_session, seed_project
):
    await seed_project()
    already = make_entry("cx_done")
    missing_one = make_entry("cx_a")
    missing_two = make_entry("cx_b")
    async_db_session.add_all([already, missing_one, missing_two])
    await async_db_session.flush()
    await embed_codex_entry(async_db_session, MockEmbeddingProvider(), already)
    existing_vector = list(already.embedding)

    written = await embed_missing_codex_entries(
        async_db_session, MockEmbeddingProvider(), project_id="proj_a", batch_size=1
    )
    await async_db_session.commit()

    assert written == 2
    assert list(already.embedding) == existing_vector
    assert missing_one.embedding is not None
    assert missing_two.embedding is not None


async def test_backfill_is_scoped_to_the_project(
    async_db_session, seed_project, make_project
):
    """回填不得跨项目 —— 会串写别人的数据。"""
    await seed_project()
    async_db_session.add(make_project("proj_other", owner_id="user_a"))
    await async_db_session.flush()
    mine = make_entry("cx_mine", project_id="proj_a")
    theirs = make_entry("cx_theirs", project_id="proj_other")
    async_db_session.add_all([mine, theirs])
    await async_db_session.flush()

    written = await embed_missing_codex_entries(
        async_db_session, MockEmbeddingProvider(), project_id="proj_a"
    )

    assert written == 1
    assert mine.embedding is not None
    assert theirs.embedding is None


async def test_backfill_of_empty_project_makes_no_calls(async_db_session, seed_project):
    await seed_project()

    assert (
        await embed_missing_codex_entries(
            async_db_session, MockEmbeddingProvider(), project_id="proj_a"
        )
        == 0
    )


async def test_backfill_rejects_invalid_batch_size(async_db_session, seed_project):
    await seed_project()

    with pytest.raises(ValueError, match="batch_size"):
        await embed_missing_codex_entries(
            async_db_session, MockEmbeddingProvider(), project_id="proj_a", batch_size=0
        )
