"""
实体链接（claim.subject_entry_id / object_entry_id）的行为测试。

方言说明：L2 精确别名匹配在 SQLite 上可验证；L3 向量兜底依赖 pgvector 的 `<=>`
运算符，本地没有 PostgreSQL，因此这里只验证「兜底被调用」与「兜底失败时降级并
计数」两类行为 —— 真实向量召回排序由 tests/integration/ 覆盖（未跑过）。
"""
import pytest
from sqlalchemy import select

from db.models_codex import CodexAlias, CodexEntry
from db.models_consistency_extended import ConsistencyClaim
from db.models_core import ChapterVersion
from services.entity_linking import ENTITY_OBJECT_TYPES, EntityLinker
from services.retrieval import ConsistencyRetrieval
from tasks import consistency as tasks


class RecordingRetrieval(ConsistencyRetrieval):
    """记录 L3 调用的检索层替身；L2 仍走真实 SQL。"""

    def __init__(self, *, candidates=None, error=None):
        super().__init__(embedding_provider=None)  # L3 被替换，不会用到 provider
        self.l3_calls: list[dict] = []
        self._candidates = candidates or {}
        self._error = error

    async def retrieve_similar_entities_l3(
        self, db, project_id, query_text, top_k=5, threshold=0.8, kinds=None,
        exclude_entry_ids=None, statuses=None,
    ):
        self.l3_calls.append(
            {
                "query_text": query_text,
                "top_k": top_k,
                "threshold": threshold,
                "kinds": kinds,
            }
        )
        if self._error:
            raise self._error
        hit = self._candidates.get(query_text)
        return [hit] if hit else []


@pytest.fixture
async def codex_fixture(async_db_session, seed_project):
    """建好 project + 若干 codex 条目与别名。"""
    await seed_project(chapter_ids=("ch_a",))

    entries = [
        ("cx_lee", "character", "李长风"),
        ("cx_wang", "character", "王二"),
        ("cx_city", "location", "青云城"),
        ("cx_sword", "item", "赤霄剑"),
    ]
    for entry_id, kind, name in entries:
        async_db_session.add(
            CodexEntry(
                id=entry_id,
                project_id="proj_a",
                kind=kind,
                name=name,
                description=f"{name} 的设定",
                attrs={},
                resident=False,
                status="active",
                ref_chapters=[],
                conflicts=[],
            )
        )
    await async_db_session.flush()

    aliases = [
        ("cx_lee", "李长风"),
        ("cx_lee", "长风"),
        ("cx_wang", "王二"),
        ("cx_city", "青云城"),
        ("cx_sword", "赤霄剑"),
    ]
    for entry_id, alias in aliases:
        async_db_session.add(CodexAlias(entry_id=entry_id, alias=alias))
    await async_db_session.flush()
    return async_db_session


# --- L2 精确别名 --------------------------------------------------------------


async def test_alias_resolves_subject_entry_id(codex_fixture, async_db_session):
    linker = EntityLinker(RecordingRetrieval(), use_vector_fallback=False)

    entry_id = await linker.resolve(async_db_session, "proj_a", "长风")

    assert entry_id == "cx_lee"
    assert linker.stats.vector_failures == 0


async def test_unknown_text_resolves_to_none(codex_fixture, async_db_session):
    linker = EntityLinker(RecordingRetrieval(), use_vector_fallback=False)

    assert await linker.resolve(async_db_session, "proj_a", "从未出现的名字") is None


async def test_blank_text_short_circuits_without_query(codex_fixture, async_db_session):
    retrieval = RecordingRetrieval()
    linker = EntityLinker(retrieval)

    assert await linker.resolve(async_db_session, "proj_a", "   ") is None
    assert await linker.resolve(async_db_session, "proj_a", None) is None
    assert retrieval.l3_calls == []


async def test_resolution_is_project_scoped(
    codex_fixture, async_db_session, make_user, make_project
):
    """别名不能跨项目命中，否则会把别人的设定连进来。"""
    async_db_session.add(make_user("user_b"))
    await async_db_session.flush()
    async_db_session.add(make_project("proj_b", owner_id="user_b"))
    await async_db_session.flush()

    linker = EntityLinker(RecordingRetrieval(), use_vector_fallback=False)
    assert await linker.resolve(async_db_session, "proj_b", "长风") is None


async def test_repeated_text_is_cached(codex_fixture, async_db_session):
    """同一批抽取内不重复调用向量层，否则一章几十条 claim 会打满网关。"""
    retrieval = RecordingRetrieval()
    linker = EntityLinker(retrieval)

    for _ in range(5):
        await linker.resolve(async_db_session, "proj_a", "未知角色")

    assert len(retrieval.l3_calls) == 1


# --- L3 向量兜底 --------------------------------------------------------------


async def test_vector_fallback_used_only_when_alias_misses(codex_fixture, async_db_session):
    retrieval = RecordingRetrieval(
        candidates={"李长风大人": {"entry_id": "cx_lee", "name": "李长风", "kind": "character"}}
    )
    linker = EntityLinker(retrieval)

    assert await linker.resolve(async_db_session, "proj_a", "长风") == "cx_lee"
    assert retrieval.l3_calls == [], "别名已精确命中，不应再走向量召回"

    assert await linker.resolve(async_db_session, "proj_a", "李长风大人") == "cx_lee"
    assert len(retrieval.l3_calls) == 1


async def test_vector_fallback_uses_configured_threshold(codex_fixture, async_db_session):
    retrieval = RecordingRetrieval()
    linker = EntityLinker(retrieval, threshold=0.91)

    await linker.resolve(async_db_session, "proj_a", "陌生名字")

    assert retrieval.l3_calls[0]["threshold"] == 0.91
    assert retrieval.l3_calls[0]["top_k"] == 1


async def test_vector_failure_degrades_and_is_counted(codex_fixture, async_db_session):
    """向量网关挂掉不应让整个抽取失败，但必须记账。"""
    retrieval = RecordingRetrieval(error=RuntimeError("embedding gateway down"))
    linker = EntityLinker(retrieval)

    assert await linker.resolve(async_db_session, "proj_a", "陌生名字") is None
    stats = linker.stats.as_dict()
    assert stats["vector_failures"] == 1
    assert "embedding gateway down" in stats["vector_error_samples"][0]


async def test_strict_mode_propagates_vector_failure(codex_fixture, async_db_session):
    retrieval = RecordingRetrieval(error=RuntimeError("embedding gateway down"))
    linker = EntityLinker(retrieval, strict=True)

    with pytest.raises(RuntimeError):
        await linker.resolve(async_db_session, "proj_a", "陌生名字")


# --- claim 级链接 -------------------------------------------------------------


async def test_entity_object_is_resolved(codex_fixture, async_db_session):
    linker = EntityLinker(RecordingRetrieval(), use_vector_fallback=False)

    subject_id, object_id = await linker.link_claim(
        async_db_session,
        "proj_a",
        {
            "subject_text": "赤霄剑",
            "predicate": "owned_by",
            "object_type": "entity",
            "object_value": "王二",
        },
    )

    assert subject_id == "cx_sword"
    assert object_id == "cx_wang"


async def test_scalar_object_is_not_resolved(codex_fixture, async_db_session):
    """scalar 的 object_value 不是实体，解析只会错连。"""
    retrieval = RecordingRetrieval()
    linker = EntityLinker(retrieval)

    subject_id, object_id = await linker.link_claim(
        async_db_session,
        "proj_a",
        {
            "subject_text": "李长风",
            "predicate": "alive",
            "object_type": "scalar",
            "object_value": "true",
        },
    )

    assert subject_id == "cx_lee"
    assert object_id is None
    assert retrieval.l3_calls == []


async def test_location_object_restricts_candidate_kinds(codex_fixture, async_db_session):
    """地点类 object 只在地点里找，避免连到同名角色。"""
    retrieval = RecordingRetrieval()
    linker = EntityLinker(retrieval)

    await linker.link_claim(
        async_db_session,
        "proj_a",
        {
            "subject_text": "李长风",
            "predicate": "located_in",
            "object_type": "location",
            "object_value": "某个没登记的地方",
        },
    )

    assert retrieval.l3_calls[0]["kinds"] == ["location", "place"]


def test_entity_object_types_cover_entity_and_location():
    assert ENTITY_OBJECT_TYPES == {"entity", "location"}


async def test_link_stats_count_resolved_and_unresolved(codex_fixture, async_db_session):
    linker = EntityLinker(RecordingRetrieval(), use_vector_fallback=False)

    await linker.link_claim(
        async_db_session,
        "proj_a",
        {"subject_text": "李长风", "predicate": "alive", "object_type": "scalar", "object_value": "true"},
    )
    await linker.link_claim(
        async_db_session,
        "proj_a",
        {"subject_text": "无名之人", "predicate": "alive", "object_type": "scalar", "object_value": "true"},
    )
    await linker.link_claim(
        async_db_session,
        "proj_a",
        {"subject_text": "赤霄剑", "predicate": "owned_by", "object_type": "entity", "object_value": "王二"},
    )

    stats = linker.stats.as_dict()
    assert stats["resolved_subjects"] == 2
    assert stats["unresolved_subjects"] == 1
    assert stats["resolved_objects"] == 1


# --- 与抽取任务的接线 ---------------------------------------------------------


async def test_extraction_persists_resolved_entry_ids(
    codex_fixture, async_db_session, make_run, monkeypatch
):
    """回归：抽取步骤原先不解析 entry_id，两个字段永远是 NULL，
    按 entry_id 分组的规则全部失效。"""
    import contextlib

    async_db_session.add(
        ChapterVersion(
            chapter_id="ch_a",
            content_html="<p>正文</p>",
            content_json={"type": "doc"},
            rev=1,
            trigger="manual",
            content_hash="h1",
        )
    )
    await async_db_session.flush()
    run = make_run(project_id="proj_a", chapter_id="ch_a", body_rev=1)
    async_db_session.add(run)
    await async_db_session.flush()

    @contextlib.asynccontextmanager
    async def _session_factory():
        yield async_db_session

    monkeypatch.setattr(tasks, "AsyncSessionLocal", _session_factory)
    monkeypatch.setattr(
        tasks,
        "build_entity_linker",
        lambda: EntityLinker(RecordingRetrieval(), use_vector_fallback=False),
    )

    class FakeProvider:
        extractor_version = "1.0.0"

        async def extract_claims(self, *, content_html, project_id, chapter_id):
            return [
                {
                    "subject_text": "长风",
                    "predicate": "alive",
                    "object_type": "scalar",
                    "object_value": "true",
                    "polarity": "positive",
                    "certainty": "explicit",
                    "confidence": 0.9,
                    "fingerprint": "fp_alive",
                },
                {
                    "subject_text": "赤霄剑",
                    "predicate": "owned_by",
                    "object_type": "entity",
                    "object_value": "王二",
                    "polarity": "positive",
                    "certainty": "explicit",
                    "confidence": 0.9,
                    "fingerprint": "fp_own",
                },
            ]

        async def generate_summary(self, *, content_html, summary_type="chapter"):
            return ("摘要", 1)

    monkeypatch.setattr(tasks, "ConsistencyProvider", FakeProvider)

    result = await tasks._extract_claims_async("task-1", run.id)

    assert result["entity_linking"]["resolved_subjects"] == 2
    assert result["entity_linking"]["resolved_objects"] == 1

    claims = {
        claim.fingerprint: claim
        for claim in (
            await async_db_session.execute(select(ConsistencyClaim))
        ).scalars().all()
    }
    assert claims["fp_alive"].subject_entry_id == "cx_lee"
    assert claims["fp_alive"].object_entry_id is None
    assert claims["fp_own"].subject_entry_id == "cx_sword"
    assert claims["fp_own"].object_entry_id == "cx_wang"
