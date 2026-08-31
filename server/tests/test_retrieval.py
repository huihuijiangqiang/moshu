"""
检索层行为测试。

方言范围：L2（精确别名）与 L4（相邻章摘要）是纯 SQL，可以在 SQLite 上真实验证。
L3 依赖 pgvector 的 `<=>` 运算符，SQLite 没有实现，相关用例在
tests/integration/test_pgvector_retrieval.py（需要真实 PostgreSQL，未跑过）。
"""
import pytest
from sqlalchemy.exc import DBAPIError

from db.models_codex import CodexAlias, CodexEntry
from db.models_consistency_extended import DocumentSummary
from services.providers import MockEmbeddingProvider
from services.retrieval import ConsistencyRetrieval


@pytest.fixture
def retrieval():
    return ConsistencyRetrieval(embedding_provider=MockEmbeddingProvider())


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


def make_summary(chapter_id: str, content: str, *, source_rev: int = 1, status: str = "active"):
    return DocumentSummary(
        owner_type="chapter",
        owner_id=chapter_id,
        source_rev=source_rev,
        summary_version="1.0.0",
        content=content,
        model_id="test-model",
        token_count=10,
        status=status,
    )


# --- L2 精确别名 ---------------------------------------------------------------


async def test_alias_exact_match_resolves_entry(async_db_session, seed_project, retrieval):
    await seed_project()
    async_db_session.add(make_entry("cx_1"))
    await async_db_session.flush()
    async_db_session.add(CodexAlias(entry_id="cx_1", alias="长风"))
    await async_db_session.flush()

    assert await retrieval.resolve_entity_by_alias_l2(async_db_session, "proj_a", "长风") == "cx_1"


async def test_alias_match_trims_whitespace(async_db_session, seed_project, retrieval):
    await seed_project()
    async_db_session.add(make_entry("cx_1"))
    await async_db_session.flush()
    async_db_session.add(CodexAlias(entry_id="cx_1", alias="长风"))
    await async_db_session.flush()

    assert await retrieval.resolve_entity_by_alias_l2(async_db_session, "proj_a", "  长风 ") == "cx_1"


async def test_alias_match_normalizes_unicode(async_db_session, seed_project, retrieval):
    """NFD 输入要能命中以 NFC 存储的别名。"""
    await seed_project()
    async_db_session.add(make_entry("cx_1"))
    await async_db_session.flush()
    async_db_session.add(CodexAlias(entry_id="cx_1", alias="Amélie"))
    await async_db_session.flush()

    decomposed = "Amélie"
    assert await retrieval.resolve_entity_by_alias_l2(async_db_session, "proj_a", decomposed) == "cx_1"


async def test_alias_lookup_is_scoped_to_project(
    async_db_session, seed_project, make_project, retrieval
):
    """别名解析不能跨项目命中。"""
    await seed_project()
    async_db_session.add(make_project("proj_other", owner_id="user_a"))
    await async_db_session.flush()
    async_db_session.add(make_entry("cx_other", project_id="proj_other"))
    await async_db_session.flush()
    async_db_session.add(CodexAlias(entry_id="cx_other", alias="长风"))
    await async_db_session.flush()

    assert await retrieval.resolve_entity_by_alias_l2(async_db_session, "proj_a", "长风") is None
    assert (
        await retrieval.resolve_entity_by_alias_l2(async_db_session, "proj_other", "长风")
        == "cx_other"
    )


async def test_unknown_alias_returns_none(async_db_session, seed_project, retrieval):
    await seed_project()

    assert await retrieval.resolve_entity_by_alias_l2(async_db_session, "proj_a", "不存在") is None


# --- entry_id 解析 -------------------------------------------------------------


async def test_resolve_entity_prefers_exact_alias(async_db_session, seed_project, retrieval):
    """精确命中时不做向量查询（SQLite 上 `<=>` 会直接报错，能证明没走 L3）。"""
    await seed_project()
    async_db_session.add(make_entry("cx_1"))
    await async_db_session.flush()
    async_db_session.add(CodexAlias(entry_id="cx_1", alias="长风"))
    await async_db_session.flush()

    assert await retrieval.resolve_entity(async_db_session, "proj_a", "长风") == "cx_1"


async def test_resolve_entity_falls_back_to_vector_search(
    async_db_session, seed_project, retrieval
):
    """没有精确别名时会走 L3；SQLite 无 `<=>`，因此这里只断言「确实尝试了向量查询」。"""
    await seed_project()

    with pytest.raises(DBAPIError):
        await retrieval.resolve_entity(async_db_session, "proj_a", "从未出现的名字")

    await async_db_session.rollback()


# --- L3 参数校验（不触库，可在 SQLite 上验证）----------------------------------


async def test_vector_search_rejects_non_positive_top_k(
    async_db_session, seed_project, retrieval
):
    await seed_project()

    with pytest.raises(ValueError, match="top_k"):
        await retrieval.retrieve_similar_entities_l3(
            async_db_session, "proj_a", "查询", top_k=0
        )


async def test_vector_search_rejects_out_of_range_threshold(
    async_db_session, seed_project, retrieval
):
    await seed_project()

    with pytest.raises(ValueError, match="threshold"):
        await retrieval.retrieve_similar_entities_l3(
            async_db_session, "proj_a", "查询", threshold=1.5
        )


async def test_vector_search_short_circuits_on_blank_query(
    async_db_session, seed_project, retrieval
):
    """空查询直接返回空，不浪费一次 embedding 调用，也不发 SQL。"""
    await seed_project()

    assert await retrieval.retrieve_similar_entities_l3(async_db_session, "proj_a", "   ") == []


# --- L4 相邻章摘要 -------------------------------------------------------------


async def test_adjacent_summaries_return_neighbours_in_idx_order(
    async_db_session, seed_project, retrieval
):
    """Chapter 的排序列是 idx（不是 sort_order），前后各 window 章。"""
    await seed_project(chapter_ids=("ch_1", "ch_2", "ch_3", "ch_4", "ch_5"))
    for chapter_id in ("ch_1", "ch_2", "ch_3", "ch_4", "ch_5"):
        async_db_session.add(make_summary(chapter_id, f"{chapter_id} 摘要"))
    await async_db_session.flush()

    results = await retrieval.retrieve_adjacent_summaries_l4(async_db_session, "ch_3", window=1)

    assert [row["chapter_id"] for row in results] == ["ch_2", "ch_4"]
    assert [row["position"] for row in results] == ["before", "after"]
    assert results[0]["summary"] == "ch_2 摘要"


async def test_adjacent_summaries_handle_sparse_idx_values(
    async_db_session, seed_project, make_chapter, retrieval
):
    """idx 是稀疏排序值，不能当成连续序号做 idx±window 过滤。"""
    await seed_project(chapter_ids=())
    for offset, chapter_id in enumerate(("ch_1", "ch_2", "ch_3")):
        # 间隔远大于 window，旧实现的 idx±window 过滤会一个邻居都取不到
        async_db_session.add(
            make_chapter(chapter_id, project_id="proj_a", idx=100_000 * (offset + 1))
        )
    await async_db_session.flush()
    async_db_session.add(make_summary("ch_1", "第一章摘要"))
    async_db_session.add(make_summary("ch_3", "第三章摘要"))
    await async_db_session.flush()

    results = await retrieval.retrieve_adjacent_summaries_l4(async_db_session, "ch_2", window=1)

    assert [row["chapter_id"] for row in results] == ["ch_1", "ch_3"]


async def test_adjacent_summaries_window_is_respected(
    async_db_session, seed_project, retrieval
):
    await seed_project(chapter_ids=("ch_1", "ch_2", "ch_3", "ch_4", "ch_5"))
    await async_db_session.flush()

    results = await retrieval.retrieve_adjacent_summaries_l4(async_db_session, "ch_3", window=2)

    assert [row["chapter_id"] for row in results] == ["ch_1", "ch_2", "ch_4", "ch_5"]


async def test_adjacent_summaries_clamp_at_first_chapter(
    async_db_session, seed_project, retrieval
):
    await seed_project(chapter_ids=("ch_1", "ch_2", "ch_3"))
    await async_db_session.flush()

    results = await retrieval.retrieve_adjacent_summaries_l4(async_db_session, "ch_1", window=2)

    assert [row["chapter_id"] for row in results] == ["ch_2", "ch_3"]
    assert all(row["position"] == "after" for row in results)


async def test_adjacent_summaries_are_scoped_to_project(
    async_db_session, seed_project, make_project, make_chapter, retrieval
):
    await seed_project(chapter_ids=("ch_1", "ch_2"))
    async_db_session.add(make_project("proj_other", owner_id="user_a"))
    await async_db_session.flush()
    async_db_session.add(make_chapter("ch_other", project_id="proj_other", idx=1024))
    await async_db_session.flush()

    results = await retrieval.retrieve_adjacent_summaries_l4(async_db_session, "ch_1", window=5)

    assert [row["chapter_id"] for row in results] == ["ch_2"]


async def test_superseded_summaries_are_not_returned(
    async_db_session, seed_project, retrieval
):
    """只取 active 摘要 —— superseded/stale 的内容对应旧正文版本。"""
    await seed_project(chapter_ids=("ch_1", "ch_2"))
    async_db_session.add(make_summary("ch_2", "旧摘要", source_rev=1, status="superseded"))
    await async_db_session.flush()

    results = await retrieval.retrieve_adjacent_summaries_l4(async_db_session, "ch_1", window=1)

    assert [row["summary"] for row in results] == [""]


async def test_latest_active_summary_revision_wins(async_db_session, seed_project, retrieval):
    await seed_project(chapter_ids=("ch_1", "ch_2"))
    async_db_session.add(make_summary("ch_2", "旧版", source_rev=1, status="superseded"))
    async_db_session.add(make_summary("ch_2", "新版", source_rev=2, status="active"))
    await async_db_session.flush()

    results = await retrieval.retrieve_adjacent_summaries_l4(async_db_session, "ch_1", window=1)

    assert results[0]["summary"] == "新版"


async def test_missing_chapter_returns_empty(async_db_session, seed_project, retrieval):
    await seed_project()

    assert await retrieval.retrieve_adjacent_summaries_l4(async_db_session, "ch_missing") == []
