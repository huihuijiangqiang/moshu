"""
pgvector L3 向量召回的集成测试。

⚠️ 本文件在本次开发中**从未运行过**：需要真实 PostgreSQL + pgvector。
tests/test_retrieval.py 的 L3 测试只能验证参数校验与短路逻辑，因为 SQLite 上
embedding 列是 TEXT，`<=>` 运算符不存在。真正的距离计算、排序与阈值裁剪只能在
这里验证。
"""
import pytest
from sqlalchemy import select

from db.models_codex import CodexAlias, CodexEntry
from db.models_core import Project, User
from services.retrieval import ConsistencyRetrieval
from tests.integration.conftest import requires_postgres

pytestmark = [pytest.mark.postgres, requires_postgres]

DIMENSIONS = 1536


def unit_vector(index: int) -> list[float]:
    """第 index 维为 1 的单位向量：两两正交，余弦距离恒为 1。"""
    vector = [0.0] * DIMENSIONS
    vector[index % DIMENSIONS] = 1.0
    return vector


def blend(index_a: int, index_b: int, weight: float) -> list[float]:
    """在两个单位向量之间插值，用来构造可预测的相似度梯度。"""
    vector = [0.0] * DIMENSIONS
    vector[index_a % DIMENSIONS] = 1.0 - weight
    vector[index_b % DIMENSIONS] = weight
    norm = (vector[index_a % DIMENSIONS] ** 2 + vector[index_b % DIMENSIONS] ** 2) ** 0.5
    return [value / norm for value in vector]


class StubEmbeddingProvider:
    """把查询文本映射到预设向量，隔离网关，只测数据库侧的距离计算。"""

    def __init__(self, vectors: dict[str, list[float]]):
        self._vectors = vectors

    async def embed_text(self, text: str, model=None) -> list[float]:
        return self._vectors[text]

    async def embed_batch(self, texts: list[str], model=None) -> list[list[float]]:
        return [self._vectors[text] for text in texts]


@pytest.fixture
async def seeded(pg_session):
    pg_session.add(
        User(
            id="user_pg",
            name="pg",
            email="pg@example.test",
            plan="free",
            quota_remaining=100,
            quota_total=100,
        )
    )
    await pg_session.flush()
    pg_session.add(
        Project(
            id="proj_pg",
            owner_id="user_pg",
            title="PG 项目",
            status="ongoing",
            target_words_daily=3000,
        )
    )
    await pg_session.flush()

    entries = [
        ("cx_near", "character", "近似角色", blend(0, 1, 0.1)),
        ("cx_mid", "character", "中等角色", blend(0, 1, 0.5)),
        ("cx_far", "location", "远处地点", unit_vector(1)),
    ]
    for entry_id, kind, name, embedding in entries:
        pg_session.add(
            CodexEntry(
                id=entry_id,
                project_id="proj_pg",
                kind=kind,
                name=name,
                description="",
                attrs={},
                resident=False,
                status="confirmed",
                ref_chapters=[],
                conflicts=[],
                embedding=embedding,
            )
        )
    await pg_session.flush()
    pg_session.add(CodexAlias(entry_id="cx_near", alias="近似角色"))
    await pg_session.commit()
    return pg_session


async def test_embedding_round_trips_as_a_vector(seeded):
    """写进去的是真实 vector 类型，读回来仍是等长浮点列表。"""
    entry = (
        await seeded.execute(select(CodexEntry).where(CodexEntry.id == "cx_near"))
    ).scalar_one()
    assert len(entry.embedding) == DIMENSIONS
    assert pytest.approx(entry.embedding[0], abs=1e-6) == blend(0, 1, 0.1)[0]


async def test_l3_orders_candidates_by_cosine_distance(seeded):
    """`<=>` 排序：越接近查询向量的排前面。SQLite 上无法验证这一点。"""
    retrieval = ConsistencyRetrieval(StubEmbeddingProvider({"查询": unit_vector(0)}))

    results = await retrieval.retrieve_similar_entities_l3(
        seeded, "proj_pg", "查询", top_k=5, threshold=0.0
    )

    assert [row["entry_id"] for row in results][:2] == ["cx_near", "cx_mid"]
    assert results[0]["distance"] < results[1]["distance"]


async def test_l3_threshold_filters_out_distant_entries(seeded):
    retrieval = ConsistencyRetrieval(StubEmbeddingProvider({"查询": unit_vector(0)}))

    results = await retrieval.retrieve_similar_entities_l3(
        seeded, "proj_pg", "查询", top_k=5, threshold=0.9
    )

    assert [row["entry_id"] for row in results] == ["cx_near"]


async def test_l3_respects_top_k(seeded):
    retrieval = ConsistencyRetrieval(StubEmbeddingProvider({"查询": unit_vector(0)}))

    results = await retrieval.retrieve_similar_entities_l3(
        seeded, "proj_pg", "查询", top_k=1, threshold=0.0
    )

    assert len(results) == 1


async def test_l3_kind_filter_excludes_other_kinds(seeded):
    retrieval = ConsistencyRetrieval(StubEmbeddingProvider({"查询": unit_vector(1)}))

    results = await retrieval.retrieve_similar_entities_l3(
        seeded, "proj_pg", "查询", top_k=5, threshold=0.0, kinds=["location"]
    )

    assert [row["entry_id"] for row in results] == ["cx_far"]


async def test_l3_exclusion_list_is_applied(seeded):
    retrieval = ConsistencyRetrieval(StubEmbeddingProvider({"查询": unit_vector(0)}))

    results = await retrieval.retrieve_similar_entities_l3(
        seeded, "proj_pg", "查询", top_k=5, threshold=0.0, exclude_entry_ids=["cx_near"]
    )

    assert "cx_near" not in [row["entry_id"] for row in results]


async def test_entries_without_embedding_are_skipped(seeded):
    seeded.add(
        CodexEntry(
            id="cx_novec",
            project_id="proj_pg",
            kind="character",
            name="无向量",
            description="",
            attrs={},
            resident=False,
            status="confirmed",
            ref_chapters=[],
            conflicts=[],
            embedding=None,
        )
    )
    await seeded.commit()
    retrieval = ConsistencyRetrieval(StubEmbeddingProvider({"查询": unit_vector(0)}))

    results = await retrieval.retrieve_similar_entities_l3(
        seeded, "proj_pg", "查询", top_k=10, threshold=0.0
    )

    assert "cx_novec" not in [row["entry_id"] for row in results]


async def test_resolve_entity_falls_back_to_vector_search(seeded):
    """别名没命中时走 L3；这条路径在 SQLite 上会因 `<=>` 直接报错。"""
    retrieval = ConsistencyRetrieval(StubEmbeddingProvider({"没登记的写法": unit_vector(0)}))

    entry_id = await retrieval.resolve_entity(
        seeded, "proj_pg", "没登记的写法", threshold=0.8
    )

    assert entry_id == "cx_near"


async def test_entity_linker_uses_real_vector_fallback(seeded):
    """实体链接的降级路径在真实 pgvector 上不应触发 vector_failures。"""
    from services.entity_linking import EntityLinker

    retrieval = ConsistencyRetrieval(StubEmbeddingProvider({"没登记的写法": unit_vector(0)}))
    linker = EntityLinker(retrieval, threshold=0.8)

    entry_id = await linker.resolve(seeded, "proj_pg", "没登记的写法")

    assert entry_id == "cx_near"
    assert linker.stats.as_dict()["vector_failures"] == 0
