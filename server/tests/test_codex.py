"""
设定库 CRUD 与 embedding 生命周期的行为测试。

覆盖：
* 「无变化不重算」的判据是可检索文本 —— 改 attrs、重复加别名都不该调网关；
* 创建/改名/改类型/改描述/别名增删都会重算；
* 网关失败时条目照样落库，状态为「待重算」（deferred），作者的编辑不回滚；
* 回填只挑待重算的行，包括**向量非空但内容已变**的那些（旧实现只看
  embedding IS NULL，这类行永远补不上）；
* 端点鉴权与租户隔离；回填任务的重试配置与幂等性。

方言说明：Vector 列在 SQLite 上编译成 TEXT，写入/读回真实有效，所以哈希与
「调了几次网关」都能在这里验证。cosine_distance 查询只有 PostgreSQL 能跑。
"""
import pytest

from api.codex import get_embedding_provider
from db.models_codex import CodexAlias, CodexEntry
from services.codex import (
    add_alias,
    count_stale_entries,
    create_entry,
    new_entry_id,
    normalize_alias,
    normalize_aliases,
    refresh_embedding_if_stale,
    remove_alias,
    update_entry,
)
from services.codex_embedding import (
    embed_codex_entry,
    embed_missing_codex_entries,
    embedding_text_hash,
    entry_embedding_text,
    is_embedding_fresh,
)
from services.embedding import EmbeddingProviderError
from services.providers import MockEmbeddingProvider


class CountingProvider(MockEmbeddingProvider):
    """记录网关调用次数 —— 「无变化不重算」只能靠调用次数证明。"""

    def __init__(self):
        self.text_calls: list[str] = []
        self.batch_calls: list[list[str]] = []

    async def embed_text(self, text, model=None):
        self.text_calls.append(text)
        return await super().embed_text(text, model)

    async def embed_batch(self, texts, model=None):
        self.batch_calls.append(list(texts))
        return await super().embed_batch(texts, model)

    @property
    def call_count(self) -> int:
        return len(self.text_calls) + sum(len(batch) for batch in self.batch_calls)


class FailingProvider(MockEmbeddingProvider):
    """网关不可用。"""

    async def embed_text(self, text, model=None):
        raise EmbeddingProviderError("gateway unavailable")

    async def embed_batch(self, texts, model=None):
        raise EmbeddingProviderError("gateway unavailable")


@pytest.fixture
def provider():
    return CountingProvider()


@pytest.fixture(autouse=True)
def small_dimensions(monkeypatch):
    """维度调小，测试里不必构造 1536 维向量。"""
    from config import settings

    monkeypatch.setattr(settings, "embedding_dimensions", 4)


async def aliases_of(db, entry_id: str) -> list[str]:
    from sqlalchemy import select

    result = await db.execute(
        select(CodexAlias.alias).where(CodexAlias.entry_id == entry_id).order_by(CodexAlias.alias)
    )
    return list(result.scalars().all())


async def reload_entry(db, entry_id: str) -> CodexEntry:
    from sqlalchemy import select

    return (
        await db.execute(select(CodexEntry).where(CodexEntry.id == entry_id))
    ).scalar_one()


# --- 标识与别名规范化 -----------------------------------------------------------


def test_entry_id_follows_the_house_convention():
    """与 GuardIssue 的 gi_ 前缀同一套：前缀 + token_hex(12)。"""
    entry_id = new_entry_id()

    assert entry_id.startswith("cx_")
    assert len(entry_id) == len("cx_") + 24
    assert entry_id != new_entry_id()


def test_alias_normalization_matches_the_l2_lookup():
    """写入端必须与 retrieval L2 用同一套规范化。

    L2 查的是 NFC(strip(text))；写入端不做同样处理，含兼容字符或首尾空白的
    别名就永远命中不了自己 —— 精确匹配层白建。
    """
    assert normalize_alias("  长风  ") == "长风"
    # NFD 分解形式（e + U+0301 组合尖音符）必须合成成单码点，否则同一个名字的
    # 两种写法在库里是两个不同的别名，L2 用哪一种都可能查不到另一种。
    decomposed = "René"
    composed = "René"
    assert decomposed != composed
    assert normalize_alias(decomposed) == composed
    assert normalize_alias(f"  {decomposed} ") == normalize_alias(composed)


def test_alias_normalization_deduplicates_and_keeps_order():
    assert normalize_aliases(["长风", "  长风  ", "李剑仙", "", "   "]) == ["长风", "李剑仙"]


# --- 创建：条目落库即待重算 ------------------------------------------------------


async def test_a_new_entry_starts_stale(async_db_session, seed_project):
    """新条目哈希为空 —— 网关调用失败也不会漏掉这一行。"""
    await seed_project()

    entry = await create_entry(
        async_db_session,
        project_id="proj_a",
        kind="character",
        name="李长风",
        description="剑修",
        aliases=["长风"],
    )
    await async_db_session.commit()

    assert entry.embedding is None
    assert entry.embedding_text_hash is None
    assert await count_stale_entries(async_db_session, "proj_a") == 1


async def test_create_entry_stores_normalized_aliases(async_db_session, seed_project):
    await seed_project()

    entry = await create_entry(
        async_db_session,
        project_id="proj_a",
        kind="character",
        name="李长风",
        aliases=["  长风  ", "长风", "李剑仙"],
    )
    await async_db_session.commit()

    assert await aliases_of(async_db_session, entry.id) == ["李剑仙", "长风"]


async def test_creating_and_embedding_makes_the_entry_fresh(
    async_db_session, seed_project, provider
):
    await seed_project()
    entry = await create_entry(
        async_db_session, project_id="proj_a", kind="character", name="李长风"
    )
    await async_db_session.commit()

    assert await refresh_embedding_if_stale(async_db_session, provider, entry) == "updated"
    await async_db_session.commit()

    assert entry.embedding is not None
    assert entry.embedding_text_hash == embedding_text_hash(
        await entry_embedding_text(async_db_session, entry)
    )
    assert await count_stale_entries(async_db_session, "proj_a") == 0


# --- 无变化不重算 ---------------------------------------------------------------


async def test_a_field_outside_the_embedding_text_does_not_recompute(
    async_db_session, seed_project, provider
):
    """attrs / resident 不参与向量化，改它们一次网关都不该调。"""
    await seed_project()
    entry = await create_entry(
        async_db_session, project_id="proj_a", kind="character", name="李长风"
    )
    await async_db_session.commit()
    await refresh_embedding_if_stale(async_db_session, provider, entry)
    await async_db_session.commit()
    calls_before = provider.call_count

    changed = await update_entry(
        async_db_session, entry, {"attrs": {"level": 5}, "resident": True}
    )
    await async_db_session.commit()
    status = await refresh_embedding_if_stale(async_db_session, provider, entry)

    assert changed is False
    assert status == "fresh"
    assert provider.call_count == calls_before


async def test_rewriting_a_field_with_the_same_value_does_not_recompute(
    async_db_session, seed_project, provider
):
    """把描述改成一模一样的内容不是「变化」—— 判据是文本，不是「谁被赋值了」。"""
    await seed_project()
    entry = await create_entry(
        async_db_session, project_id="proj_a", kind="character", name="李长风", description="剑修"
    )
    await async_db_session.commit()
    await refresh_embedding_if_stale(async_db_session, provider, entry)
    await async_db_session.commit()
    calls_before = provider.call_count

    changed = await update_entry(async_db_session, entry, {"description": "剑修"})
    await async_db_session.commit()

    assert changed is False
    assert provider.call_count == calls_before


async def test_re_adding_an_existing_alias_changes_nothing(
    async_db_session, seed_project, provider
):
    """重复添加同一别名（哪怕空白不同）不加行、不标脏、不调网关。"""
    await seed_project()
    entry = await create_entry(
        async_db_session, project_id="proj_a", kind="character", name="李长风", aliases=["长风"]
    )
    await async_db_session.commit()
    await refresh_embedding_if_stale(async_db_session, provider, entry)
    await async_db_session.commit()
    calls_before = provider.call_count

    added = await add_alias(async_db_session, entry, "  长风  ")
    await async_db_session.commit()

    assert added is False
    assert await aliases_of(async_db_session, entry.id) == ["长风"]
    assert provider.call_count == calls_before


async def test_removing_an_absent_alias_changes_nothing(
    async_db_session, seed_project, provider
):
    await seed_project()
    entry = await create_entry(
        async_db_session, project_id="proj_a", kind="character", name="李长风", aliases=["长风"]
    )
    await async_db_session.commit()
    await refresh_embedding_if_stale(async_db_session, provider, entry)
    await async_db_session.commit()
    calls_before = provider.call_count

    removed = await remove_alias(async_db_session, entry, "从未存在")
    await async_db_session.commit()

    assert removed is False
    assert provider.call_count == calls_before


# --- 真的变了就重算 -------------------------------------------------------------


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "沈砚"},
        {"kind": "location"},
        {"description": "完全不同的描述"},
    ],
    ids=["name", "kind", "description"],
)
async def test_changing_an_embedded_field_recomputes(
    async_db_session, seed_project, provider, changes
):
    """名称/类型/描述都参与向量化，改任一个都要重算。"""
    await seed_project()
    entry = await create_entry(
        async_db_session, project_id="proj_a", kind="character", name="李长风", description="剑修"
    )
    await async_db_session.commit()
    await refresh_embedding_if_stale(async_db_session, provider, entry)
    await async_db_session.commit()
    first_vector = list(entry.embedding)

    changed = await update_entry(async_db_session, entry, changes)
    await async_db_session.commit()

    assert changed is True
    assert entry.embedding_text_hash is None, "标脏必须与作者的改动同一个事务落库"

    status = await refresh_embedding_if_stale(async_db_session, provider, entry)
    await async_db_session.commit()

    assert status == "updated"
    assert list(entry.embedding) != first_vector


async def test_adding_a_new_alias_recomputes(async_db_session, seed_project, provider):
    """别名参与向量化：新别名必须进向量，否则用它指代时 L3 召回不到。"""
    await seed_project()
    entry = await create_entry(
        async_db_session, project_id="proj_a", kind="character", name="李长风"
    )
    await async_db_session.commit()
    await refresh_embedding_if_stale(async_db_session, provider, entry)
    await async_db_session.commit()
    first_vector = list(entry.embedding)

    added = await add_alias(async_db_session, entry, "李剑仙")
    await async_db_session.commit()

    assert added is True
    assert entry.embedding_text_hash is None

    await refresh_embedding_if_stale(async_db_session, provider, entry)
    await async_db_session.commit()

    assert list(entry.embedding) != first_vector


async def test_removing_a_real_alias_recomputes(async_db_session, seed_project, provider):
    await seed_project()
    entry = await create_entry(
        async_db_session, project_id="proj_a", kind="character", name="李长风", aliases=["长风"]
    )
    await async_db_session.commit()
    await refresh_embedding_if_stale(async_db_session, provider, entry)
    await async_db_session.commit()
    first_vector = list(entry.embedding)

    removed = await remove_alias(async_db_session, entry, "长风")
    await async_db_session.commit()

    assert removed is True
    assert entry.embedding_text_hash is None

    await refresh_embedding_if_stale(async_db_session, provider, entry)
    await async_db_session.commit()

    assert list(entry.embedding) != first_vector


# --- 网关失败 -------------------------------------------------------------------


async def test_a_gateway_failure_leaves_the_entry_saved_and_stale(
    async_db_session, seed_project
):
    """网关挂了不该让作者的编辑失败，条目留在「待重算」状态。"""
    await seed_project()
    entry = await create_entry(
        async_db_session, project_id="proj_a", kind="character", name="李长风"
    )
    entry_id = entry.id
    await async_db_session.commit()

    status = await refresh_embedding_if_stale(async_db_session, FailingProvider(), entry)
    await async_db_session.commit()

    assert status == "deferred"
    stored = await reload_entry(async_db_session, entry_id)
    assert stored.name == "李长风", "作者的写入不能被向量失败带走"
    assert stored.embedding is None
    assert stored.embedding_text_hash is None
    assert await count_stale_entries(async_db_session, "proj_a") == 1


async def test_a_gateway_failure_after_an_edit_never_leaves_a_matching_hash(
    async_db_session, seed_project, provider
):
    """改动 + 网关失败之后，哈希不能仍然指向旧向量。

    这正是先标脏再调网关的意义：否则库里会留下「向量对不上内容」却自称新鲜的行，
    L3 按旧描述召回，而且没有任何查询能发现它。
    """
    await seed_project()
    entry = await create_entry(
        async_db_session, project_id="proj_a", kind="character", name="李长风", description="剑修"
    )
    entry_id = entry.id
    await async_db_session.commit()
    await refresh_embedding_if_stale(async_db_session, provider, entry)
    await async_db_session.commit()

    await update_entry(async_db_session, entry, {"description": "改成了完全不同的设定"})
    await async_db_session.commit()
    status = await refresh_embedding_if_stale(async_db_session, FailingProvider(), entry)
    await async_db_session.commit()

    assert status == "deferred"
    stored = await reload_entry(async_db_session, entry_id)
    assert stored.description == "改成了完全不同的设定"
    assert stored.embedding_text_hash is None
    assert await count_stale_entries(async_db_session, "proj_a") == 1


# --- 回填 -----------------------------------------------------------------------


async def test_backfill_picks_up_entries_whose_text_changed(
    async_db_session, seed_project, provider
):
    """回归：向量非空但内容已变的行也必须被回填挑中。

    旧实现只查 embedding IS NULL，这类行永远补不上 —— 列不为空，回填看不见它，
    L3 于是一直按旧描述召回。哈希标脏就是为了让它在 SQL 里可见。
    """
    await seed_project()
    entry = await create_entry(
        async_db_session, project_id="proj_a", kind="character", name="李长风", description="剑修"
    )
    await async_db_session.commit()
    await refresh_embedding_if_stale(async_db_session, provider, entry)
    await async_db_session.commit()
    stale_vector = list(entry.embedding)

    await update_entry(async_db_session, entry, {"description": "完全不同的设定"})
    await async_db_session.commit()

    assert entry.embedding is not None, "前置条件：向量列非空，只是内容过时了"

    written = await embed_missing_codex_entries(
        async_db_session, provider, project_id="proj_a"
    )
    await async_db_session.commit()

    assert written == 1
    assert list(entry.embedding) != stale_vector


async def test_backfill_skips_fresh_entries(async_db_session, seed_project, provider):
    await seed_project()
    fresh = await create_entry(
        async_db_session, project_id="proj_a", kind="character", name="李长风"
    )
    stale = await create_entry(
        async_db_session, project_id="proj_a", kind="character", name="沈砚"
    )
    await async_db_session.commit()
    await refresh_embedding_if_stale(async_db_session, provider, fresh)
    await async_db_session.commit()
    fresh_vector = list(fresh.embedding)

    written = await embed_missing_codex_entries(
        async_db_session, provider, project_id="proj_a"
    )
    await async_db_session.commit()

    assert written == 1
    assert list(fresh.embedding) == fresh_vector
    assert stale.embedding is not None


async def test_backfill_commits_each_batch_so_retries_skip_finished_work(
    async_db_session, seed_project, provider
):
    """逐批提交 + 哈希判据 = 重试幂等：跑过的批次第二轮直接被跳过。"""
    await seed_project()
    for index in range(4):
        await create_entry(
            async_db_session, project_id="proj_a", kind="character", name=f"角色{index}"
        )
    await async_db_session.commit()

    first = await embed_missing_codex_entries(
        async_db_session, provider, project_id="proj_a", batch_size=2, commit_each_batch=True
    )
    calls_after_first = provider.call_count

    second = await embed_missing_codex_entries(
        async_db_session, provider, project_id="proj_a", batch_size=2, commit_each_batch=True
    )

    assert first == 4
    assert second == 0, "第二轮不该重做已完成的批次"
    assert provider.call_count == calls_after_first


async def test_backfill_is_scoped_to_one_project(
    async_db_session, seed_project, make_project, provider
):
    """回填不得跨项目 —— 会串写别人的数据，也会烧别人的配额。"""
    await seed_project()
    async_db_session.add(make_project("proj_other", owner_id="user_a"))
    await async_db_session.flush()
    mine = await create_entry(
        async_db_session, project_id="proj_a", kind="character", name="李长风"
    )
    theirs = await create_entry(
        async_db_session, project_id="proj_other", kind="character", name="沈砚"
    )
    await async_db_session.commit()

    written = await embed_missing_codex_entries(
        async_db_session, provider, project_id="proj_a"
    )
    await async_db_session.commit()

    assert written == 1
    assert mine.embedding is not None
    assert theirs.embedding is None


async def test_force_recomputes_even_when_fresh(async_db_session, seed_project, provider):
    """换 embedding 模型时要能强制重算整批。"""
    await seed_project()
    entry = await create_entry(
        async_db_session, project_id="proj_a", kind="character", name="李长风"
    )
    await async_db_session.commit()
    await refresh_embedding_if_stale(async_db_session, provider, entry)
    await async_db_session.commit()
    calls_before = provider.call_count

    assert await embed_codex_entry(async_db_session, provider, entry, force=True) is True
    assert provider.call_count == calls_before + 1


async def test_is_embedding_fresh_rejects_a_hash_without_a_vector(
    async_db_session, seed_project
):
    """哈希在、向量没了（例如手工清库）→ 仍算过时。"""
    await seed_project()
    entry = await create_entry(
        async_db_session, project_id="proj_a", kind="character", name="李长风"
    )
    await async_db_session.commit()
    text = await entry_embedding_text(async_db_session, entry)
    entry.embedding_text_hash = embedding_text_hash(text)

    assert is_embedding_fresh(entry, text) is False


# --- API 端点 -------------------------------------------------------------------


@pytest.fixture
def codex_client(app_client, provider):
    """把 embedding provider 依赖换成计数 mock（端点测试不发网络请求）。"""
    from main import app

    app.dependency_overrides[get_embedding_provider] = lambda: provider
    yield app_client
    app.dependency_overrides.pop(get_embedding_provider, None)


@pytest.fixture
def failing_codex_client(app_client):
    from main import app

    app.dependency_overrides[get_embedding_provider] = FailingProvider
    yield app_client
    app.dependency_overrides.pop(get_embedding_provider, None)


async def test_creating_an_entry_requires_authentication(codex_client):
    response = await codex_client.post(
        "/codex/proj_a/entries", json={"kind": "character", "name": "李长风"}
    )

    assert response.status_code in (401, 403)


async def test_creating_an_entry_in_someone_elses_project_is_denied(
    codex_client, async_db_session, seed_project, make_user, auth_headers
):
    """跨租户写入 → 403，且什么都不写。"""
    await seed_project()
    async_db_session.add(make_user("user_b"))
    await async_db_session.commit()

    response = await codex_client.post(
        "/codex/proj_a/entries",
        json={"kind": "character", "name": "李长风"},
        headers=auth_headers("user_b"),
    )

    assert response.status_code == 403
    assert await count_stale_entries(async_db_session, "proj_a") == 0


async def test_creating_an_entry_returns_it_with_the_embedding_settled(
    codex_client, async_db_session, seed_project, auth_headers, provider
):
    await seed_project()
    await async_db_session.commit()

    response = await codex_client.post(
        "/codex/proj_a/entries",
        json={
            "kind": "character",
            "name": "李长风",
            "description": "剑修",
            "aliases": ["  长风  ", "长风"],
        },
        headers=auth_headers("user_a"),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "李长风"
    assert body["aliases"] == ["长风"], "别名要规范化并去重"
    assert body["embedding_status"] == "updated"
    assert provider.call_count == 1
    stored = await reload_entry(async_db_session, body["id"])
    assert stored.embedding is not None


async def test_an_invalid_kind_is_rejected_before_any_write(
    codex_client, async_db_session, seed_project, auth_headers
):
    """kind 不在受控取值里 → 422，不落库。"""
    await seed_project()
    await async_db_session.commit()

    response = await codex_client.post(
        "/codex/proj_a/entries",
        json={"kind": "spaceship", "name": "李长风"},
        headers=auth_headers("user_a"),
    )

    assert response.status_code == 422
    assert await count_stale_entries(async_db_session, "proj_a") == 0


async def test_an_invalid_status_is_rejected(
    codex_client, async_db_session, seed_project, auth_headers
):
    await seed_project()
    await async_db_session.commit()

    response = await codex_client.post(
        "/codex/proj_a/entries",
        json={"kind": "character", "name": "李长风", "status": "half_confirmed"},
        headers=auth_headers("user_a"),
    )

    assert response.status_code == 422


async def test_a_gateway_failure_still_creates_the_entry(
    failing_codex_client, async_db_session, seed_project, auth_headers
):
    """网关挂了照样 201，状态是 deferred —— 作者的编辑不被网关拖死。"""
    await seed_project()
    await async_db_session.commit()

    response = await failing_codex_client.post(
        "/codex/proj_a/entries",
        json={"kind": "character", "name": "李长风"},
        headers=auth_headers("user_a"),
    )

    assert response.status_code == 201
    assert response.json()["embedding_status"] == "deferred"
    stored = await reload_entry(async_db_session, response.json()["id"])
    assert stored.name == "李长风"
    assert stored.embedding is None


async def test_updating_a_non_embedded_field_reports_fresh(
    codex_client, async_db_session, seed_project, auth_headers, provider
):
    """attrs 改动 → fresh，一次网关调用都不该发生。"""
    await seed_project()
    entry = await create_entry(
        async_db_session, project_id="proj_a", kind="character", name="李长风"
    )
    await async_db_session.commit()
    await refresh_embedding_if_stale(async_db_session, provider, entry)
    await async_db_session.commit()
    calls_before = provider.call_count

    response = await codex_client.patch(
        f"/codex/proj_a/entries/{entry.id}",
        json={"attrs": {"level": 5}},
        headers=auth_headers("user_a"),
    )

    assert response.status_code == 200
    assert response.json()["embedding_status"] == "fresh"
    assert provider.call_count == calls_before


async def test_updating_the_name_recomputes_the_embedding(
    codex_client, async_db_session, seed_project, auth_headers, provider
):
    await seed_project()
    entry = await create_entry(
        async_db_session, project_id="proj_a", kind="character", name="李长风"
    )
    await async_db_session.commit()
    await refresh_embedding_if_stale(async_db_session, provider, entry)
    await async_db_session.commit()
    calls_before = provider.call_count

    response = await codex_client.patch(
        f"/codex/proj_a/entries/{entry.id}",
        json={"name": "沈砚"},
        headers=auth_headers("user_a"),
    )

    assert response.status_code == 200
    assert response.json()["embedding_status"] == "updated"
    assert response.json()["name"] == "沈砚"
    assert provider.call_count == calls_before + 1


async def test_updating_an_entry_from_another_project_is_a_404(
    codex_client, async_db_session, seed_project, make_project, auth_headers
):
    """按 id 直取但项目不匹配 → 404，不能跨项目改。"""
    await seed_project()
    async_db_session.add(make_project("proj_other", owner_id="user_a"))
    await async_db_session.flush()
    entry = await create_entry(
        async_db_session, project_id="proj_other", kind="character", name="沈砚"
    )
    await async_db_session.commit()

    response = await codex_client.patch(
        f"/codex/proj_a/entries/{entry.id}",
        json={"name": "改名"},
        headers=auth_headers("user_a"),
    )

    assert response.status_code == 404


async def test_adding_an_alias_through_the_api_recomputes_once(
    codex_client, async_db_session, seed_project, auth_headers, provider
):
    await seed_project()
    entry = await create_entry(
        async_db_session, project_id="proj_a", kind="character", name="李长风"
    )
    await async_db_session.commit()
    await refresh_embedding_if_stale(async_db_session, provider, entry)
    await async_db_session.commit()
    calls_before = provider.call_count

    response = await codex_client.post(
        f"/codex/proj_a/entries/{entry.id}/aliases",
        json={"alias": "李剑仙"},
        headers=auth_headers("user_a"),
    )

    assert response.status_code == 201
    assert response.json() == {
        "alias": "李剑仙",
        "changed": True,
        "embedding_status": "updated",
    }
    assert provider.call_count == calls_before + 1
    assert await aliases_of(async_db_session, entry.id) == ["李剑仙"]


async def test_adding_a_duplicate_alias_through_the_api_is_a_noop(
    codex_client, async_db_session, seed_project, auth_headers, provider
):
    await seed_project()
    entry = await create_entry(
        async_db_session, project_id="proj_a", kind="character", name="李长风", aliases=["长风"]
    )
    await async_db_session.commit()
    await refresh_embedding_if_stale(async_db_session, provider, entry)
    await async_db_session.commit()
    calls_before = provider.call_count

    response = await codex_client.post(
        f"/codex/proj_a/entries/{entry.id}/aliases",
        json={"alias": "  长风  "},
        headers=auth_headers("user_a"),
    )

    assert response.status_code == 201
    assert response.json()["changed"] is False
    assert response.json()["embedding_status"] == "fresh"
    assert provider.call_count == calls_before
    assert await aliases_of(async_db_session, entry.id) == ["长风"]


async def test_removing_an_alias_through_the_api(
    codex_client, async_db_session, seed_project, auth_headers, provider
):
    await seed_project()
    entry = await create_entry(
        async_db_session, project_id="proj_a", kind="character", name="李长风", aliases=["长风"]
    )
    await async_db_session.commit()
    await refresh_embedding_if_stale(async_db_session, provider, entry)
    await async_db_session.commit()

    response = await codex_client.request(
        "DELETE",
        f"/codex/proj_a/entries/{entry.id}/aliases",
        json={"alias": "长风"},
        headers=auth_headers("user_a"),
    )

    assert response.status_code == 200
    assert response.json()["changed"] is True
    assert response.json()["embedding_status"] == "updated"
    assert await aliases_of(async_db_session, entry.id) == []


async def test_the_backfill_endpoint_requires_project_access(
    codex_client, async_db_session, seed_project, make_user, auth_headers
):
    """回填会打满网关配额 —— 必须过项目鉴权。"""
    await seed_project()
    async_db_session.add(make_user("user_b"))
    await async_db_session.commit()

    response = await codex_client.post(
        "/codex/proj_a/backfill-embeddings", headers=auth_headers("user_b")
    )

    assert response.status_code == 403


async def test_the_backfill_endpoint_reports_what_is_left(
    codex_client, async_db_session, seed_project, auth_headers
):
    """回填后 remaining_count 归零 —— 这是「还欠多少账」的可见信号。"""
    await seed_project()
    for index in range(3):
        await create_entry(
            async_db_session, project_id="proj_a", kind="character", name=f"角色{index}"
        )
    await async_db_session.commit()

    response = await codex_client.post(
        "/codex/proj_a/backfill-embeddings", headers=auth_headers("user_a")
    )

    assert response.status_code == 200
    assert response.json() == {
        "project_id": "proj_a",
        "embedded_count": 3,
        "remaining_count": 0,
    }


async def test_a_failed_backfill_leaves_the_debt_visible(
    failing_codex_client, async_db_session, seed_project, auth_headers
):
    """网关挂了，回填端点必须报错而不是假装成功。

    创建端点可以降级成 deferred（作者在等），但回填是运维操作：静默返回
    embedded_count=0 会让人以为账已经还完了。
    """
    await seed_project()
    await create_entry(
        async_db_session, project_id="proj_a", kind="character", name="李长风"
    )
    await async_db_session.commit()

    with pytest.raises(EmbeddingProviderError):
        await failing_codex_client.post(
            "/codex/proj_a/backfill-embeddings", headers=auth_headers("user_a")
        )

    assert await count_stale_entries(async_db_session, "proj_a") == 1


async def test_the_backfill_endpoint_rejects_a_bad_batch_size(
    codex_client, async_db_session, seed_project, auth_headers
):
    await seed_project()
    await async_db_session.commit()

    response = await codex_client.post(
        "/codex/proj_a/backfill-embeddings?batch_size=0", headers=auth_headers("user_a")
    )

    assert response.status_code == 422


# --- 回填任务 -------------------------------------------------------------------


def test_the_backfill_task_retries_with_bounded_exponential_backoff():
    """架构 8 要求指数退避与最大重试 —— 这些是任务注册时就固定下来的配置。"""
    from tasks.codex import RETRYABLE_ERRORS, backfill_codex_embeddings_task

    assert backfill_codex_embeddings_task.name == "codex.backfill_embeddings"
    assert backfill_codex_embeddings_task.max_retries == 5
    assert backfill_codex_embeddings_task.retry_backoff is True
    assert backfill_codex_embeddings_task.retry_backoff_max == 600
    assert backfill_codex_embeddings_task.retry_jitter is True
    assert EmbeddingProviderError in RETRYABLE_ERRORS
    assert set(backfill_codex_embeddings_task.autoretry_for) == set(RETRYABLE_ERRORS)


async def test_the_backfill_task_body_embeds_and_reports_the_remainder(
    async_db_session, seed_project, monkeypatch, provider
):
    """任务体的真实行为：回填 + 报告剩余欠账。"""
    import contextlib

    from tasks import codex as codex_tasks

    @contextlib.asynccontextmanager
    async def _session_factory():
        yield async_db_session

    monkeypatch.setattr(codex_tasks, "AsyncSessionLocal", _session_factory)
    monkeypatch.setattr(codex_tasks, "GatewayEmbeddingProvider", lambda: provider)

    await seed_project()
    for index in range(3):
        await create_entry(
            async_db_session, project_id="proj_a", kind="character", name=f"角色{index}"
        )
    await async_db_session.commit()

    result = await codex_tasks._backfill_async("proj_a", batch_size=2)

    assert result == {
        "status": "success",
        "project_id": "proj_a",
        "embedded_count": 3,
        "remaining_count": 0,
    }
    # batch_size=2 → 两批（2 + 1），而不是三次单条调用
    assert [len(batch) for batch in provider.batch_calls] == [2, 1]


async def test_the_backfill_task_propagates_gateway_failure_for_retry(
    async_db_session, seed_project, monkeypatch
):
    """网关失败必须冒泡 —— Celery 才会按退避重试；吞掉就永远补不上。"""
    import contextlib

    from tasks import codex as codex_tasks

    @contextlib.asynccontextmanager
    async def _session_factory():
        yield async_db_session

    monkeypatch.setattr(codex_tasks, "AsyncSessionLocal", _session_factory)
    monkeypatch.setattr(codex_tasks, "GatewayEmbeddingProvider", FailingProvider)

    await seed_project()
    await create_entry(
        async_db_session, project_id="proj_a", kind="character", name="李长风"
    )
    await async_db_session.commit()

    with pytest.raises(EmbeddingProviderError):
        await codex_tasks._backfill_async("proj_a", batch_size=2)
