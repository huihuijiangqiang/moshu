"""
一致性管道任务的行为测试。

这些测试直接调用任务内部的 async 实现，并把 AsyncSessionLocal 换成测试会话，
从而在不起 Celery worker、不连 PostgreSQL 的前提下验证真实数据库行为。

重点覆盖：
* 读的是不可变快照 ChapterVersion，作者中途再存也不会串版本；
* 提交前锁住 ChapterBody 头行再校验版本，旧 worker 不会覆盖新版本结论；
* 失败抛异常（Celery chain 才会中断），而不是返回 {"status": "error"}；
* run 一旦 failed，摘要与扫描拒绝执行；
* 同版本重放是集合替换（存在的原地更新、缺失的标 superseded、新增的插入），
  一行都不删 —— 架构 4.3 要求旧 claim 只标 superseded；
* 新版本发布时旧 claim 原子作废；
* 抽取之后摘要与扫描并行投递，扫描不等摘要；
* 唯一键并发竞态用 SAVEPOINT 兜住（方言无关，SQLite 上也能验证）；
* claim 的 entry_id 落库；时间线字段只在模型给出可靠依据时落库，倒叙与未知顺序
  保持 NULL 并且不触发依赖时序的硬规则。

方言范围：ChapterBody 的 FOR UPDATE 行级锁在 SQLite 上被忽略，这里验证的是
「锁 + 版本比较」这段逻辑本身；真正的并发互斥需要 PostgreSQL，见
tests/integration/（未跑过）。
"""
import contextlib
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from config import settings
from db.models_consistency_extended import ConsistencyClaim, DocumentSummary
from db.models_core import Chapter, ChapterBody, ChapterVersion
from db.models_guard import GuardIssue
from services.consistency import (
    PIPELINE_VERSION,
    get_or_create_run,
    normalize_run_trigger,
    upsert_active_summary,
)
from services.timeline import parse_absolute_anchor
from tasks import consistency as tasks


@pytest.fixture
def use_test_session(async_db_session, monkeypatch):
    """把任务里的 AsyncSessionLocal 换成测试会话（不真正开关连接）。"""

    @contextlib.asynccontextmanager
    async def _session_factory():
        yield async_db_session

    monkeypatch.setattr(tasks, "AsyncSessionLocal", _session_factory)
    return async_db_session


@pytest.fixture(autouse=True)
def alias_only_linker(monkeypatch):
    """管道测试聚焦版本/事务行为，实体链接只用精确别名，不发网络请求。

    实体链接本身的行为由 tests/test_entity_linking.py 覆盖。
    """
    from services.entity_linking import EntityLinker
    from services.retrieval import ConsistencyRetrieval

    monkeypatch.setattr(
        tasks,
        "build_entity_linker",
        lambda: EntityLinker(
            ConsistencyRetrieval(embedding_provider=None), use_vector_fallback=False
        ),
    )


@pytest.fixture
def fake_provider(monkeypatch):
    """替换 ConsistencyProvider，可控返回值与异常。"""

    class FakeProvider:
        claims: list[dict] = []
        summary: tuple[str, int] = ("摘要内容", 42)
        extract_error: Exception | None = None
        summary_error: Exception | None = None
        seen_html: list[str] = []
        extractor_version = "1.0.0"

        async def extract_claims(self, *, content_html, project_id, chapter_id):
            FakeProvider.seen_html.append(content_html)
            if FakeProvider.extract_error:
                raise FakeProvider.extract_error
            return list(FakeProvider.claims)

        async def generate_summary(self, *, content_html, summary_type="chapter"):
            FakeProvider.seen_html.append(content_html)
            if FakeProvider.summary_error:
                raise FakeProvider.summary_error
            return FakeProvider.summary

    FakeProvider.claims = []
    FakeProvider.summary = ("摘要内容", 42)
    FakeProvider.extract_error = None
    FakeProvider.summary_error = None
    FakeProvider.seen_html = []
    monkeypatch.setattr(tasks, "ConsistencyProvider", FakeProvider)
    return FakeProvider


@pytest.fixture
async def pipeline_setup(async_db_session, seed_project, make_run):
    """project + chapter + 一版不可变快照 + ChapterBody 头行 + 一个 run。

    最后 commit：真实流程里派发任务的请求事务早已提交，worker 才看到 run。
    这里也必须提交，否则 fail_run 的「先 rollback」会把夹具数据一起回滚掉。
    """
    await seed_project(chapter_ids=("ch_a",))
    async_db_session.add(
        ChapterBody(
            chapter_id="ch_a",
            content_html="<p>第一版正文</p>",
            content_json={"type": "doc"},
            rev=1,
        )
    )
    async_db_session.add(
        ChapterVersion(
            chapter_id="ch_a",
            content_html="<p>第一版正文</p>",
            content_json={"type": "doc"},
            rev=1,
            trigger="manual",
            content_hash="hash1",
        )
    )
    await async_db_session.flush()
    run = make_run(project_id="proj_a", chapter_id="ch_a", body_rev=1)
    async_db_session.add(run)
    await async_db_session.commit()
    return run


def claim_payload(subject: str, fingerprint: str, **overrides) -> dict:
    payload = {
        "subject_text": subject,
        "predicate": "alive",
        "object_type": "scalar",
        "object_value": "true",
        "polarity": "positive",
        "certainty": "explicit",
        "confidence": 0.9,
        "fingerprint": fingerprint,
    }
    payload.update(overrides)
    return payload


#: 测试里表示「正文写明了时间」的基准日。顺序值 N 表示这一天的第 N 秒 —— 具体
#: 数值无关紧要，重要的是它来自一个**可解析的绝对时间**，而不是抽取顺序。
ORDER_EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)


def order_anchor(anchor_offset: float) -> str:
    """把测试用的相对顺序值转成正文里可能出现的绝对时间（ISO-8601）。"""
    return (ORDER_EPOCH + timedelta(seconds=anchor_offset)).strftime("%Y-%m-%dT%H:%M:%S")


def expected_order(anchor_offset: float) -> float:
    """时间线服务会依据该锚点分配的 story_order。"""
    return parse_absolute_anchor(order_anchor(anchor_offset))


def ordered_payload(subject: str, fingerprint: str, anchor_offset: float, **overrides) -> dict:
    """正文写明了绝对时间、因此**能拿到全局顺序**的 claim。

    payload 里刻意不含 story_order：模型逐块工作，编出来的序号跨块没有共同标尺，
    所以它只报可追溯的时间锚点，story_order 由 services.timeline 依据锚点分配
    （架构 4.3）。第三个参数是这个锚点在基准日内的秒偏移，方便测试表达先后 ——
    落库后的实际值用 expected_order() 换算。

    需要模拟「模型硬塞了一个序号」时，用 story_order= 覆盖 —— 它应当被覆盖掉。
    """
    payload = {
        "timeline_id": "main",
        "order_basis": "absolute_datetime",
        "temporal_anchor_text": f"基准日第 {anchor_offset:g} 秒",
        "temporal_anchor_value": order_anchor(anchor_offset),
        "order_confidence": 0.95,
    }
    payload.update(overrides)
    return claim_payload(subject, fingerprint, **payload)


async def add_version(db, *, rev: int, html: str):
    """写入新的不可变快照并推进 ChapterBody 头行版本号（模拟一次真实保存）。"""
    db.add(
        ChapterVersion(
            chapter_id="ch_a",
            content_html=html,
            content_json={"type": "doc"},
            rev=rev,
            trigger="manual",
            content_hash=f"hash{rev}",
        )
    )
    body = (
        await db.execute(select(ChapterBody).where(ChapterBody.chapter_id == "ch_a"))
    ).scalar_one_or_none()
    if body:
        body.rev = rev
        body.content_html = html
    else:
        db.add(
            ChapterBody(
                chapter_id="ch_a",
                content_html=html,
                content_json={"type": "doc"},
                rev=rev,
            )
        )
    await db.flush()


async def load_claims(db) -> list[ConsistencyClaim]:
    db.expunge_all()
    result = await db.execute(select(ConsistencyClaim).order_by(ConsistencyClaim.id))
    return list(result.scalars().all())


# --- trigger 归一化 -----------------------------------------------------------


def test_trigger_aliases_map_to_body_save():
    for raw in ("user_edit", "manual", "autosave", "accept_draft", None, "未知"):
        assert normalize_run_trigger(raw) == "body_save"


def test_already_valid_triggers_are_preserved():
    """回归：原实现无条件返回 body_save，手动扫描被记成正文保存。"""
    assert normalize_run_trigger("manual_scan") == "manual_scan"
    assert normalize_run_trigger("pipeline_upgrade") == "pipeline_upgrade"
    assert normalize_run_trigger("maintenance") == "maintenance"


# --- 不可变版本读取 -----------------------------------------------------------


async def test_extraction_reads_the_immutable_snapshot(
    use_test_session, pipeline_setup, fake_provider
):
    """读的是 ChapterVersion 快照内容。"""
    await tasks._extract_claims_async("task-1", pipeline_setup.id)

    assert fake_provider.seen_html == ["<p>第一版正文</p>"]


async def test_extraction_uses_the_runs_revision_not_the_latest(
    use_test_session, pipeline_setup, fake_provider
):
    """回归：读可变的 ChapterBody 会拿到最新正文，却按旧 body_rev 记账。"""
    await add_version(use_test_session, rev=2, html="<p>第二版正文</p>")

    # rev 1 的 run 已经过时，必须失败而不是拿第二版正文冒充第一版
    with pytest.raises(tasks.StaleRevisionError):
        await tasks._extract_claims_async("task-1", pipeline_setup.id)

    assert fake_provider.seen_html == []


async def test_body_head_revision_alone_marks_the_run_stale(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """只有 ChapterBody 头行推进（快照还没落库）也必须判为过期。

    保存流程可能先更新头行再写快照；只看 max(ChapterVersion.rev) 会漏掉这个窗口。
    """
    body = (
        await async_db_session.execute(
            select(ChapterBody).where(ChapterBody.chapter_id == "ch_a")
        )
    ).scalar_one()
    body.rev = 2
    await async_db_session.flush()

    with pytest.raises(tasks.StaleRevisionError):
        await tasks._extract_claims_async("task-1", pipeline_setup.id)


async def test_head_revision_reads_both_sources(
    use_test_session, async_db_session, pipeline_setup
):
    """head_revision 取头行与快照表的较大者。"""
    assert await tasks.head_revision(async_db_session, "ch_a") == 1

    async_db_session.add(
        ChapterVersion(
            chapter_id="ch_a",
            content_html="<p>v7</p>",
            content_json={"type": "doc"},
            rev=7,
            trigger="manual",
            content_hash="hash7",
        )
    )
    await async_db_session.flush()
    assert await tasks.head_revision(async_db_session, "ch_a") == 7, "快照更新"

    body = (
        await async_db_session.execute(
            select(ChapterBody).where(ChapterBody.chapter_id == "ch_a")
        )
    ).scalar_one()
    body.rev = 9
    await async_db_session.flush()
    assert await tasks.head_revision(async_db_session, "ch_a") == 9, "头行更新"


async def test_lock_body_head_returns_the_head_row(
    use_test_session, async_db_session, pipeline_setup
):
    """提交前锁的是 ChapterBody 头行（每章唯一的可变行、rev 乐观锁载体）。"""
    locked = await tasks.lock_body_head(async_db_session, "ch_a")

    assert locked is not None
    assert locked.chapter_id == "ch_a"
    assert locked.rev == 1


async def test_missing_snapshot_fails_the_run(
    use_test_session, async_db_session, seed_project, make_run, fake_provider
):
    await seed_project(chapter_ids=("ch_a",))
    run = make_run(project_id="proj_a", chapter_id="ch_a", body_rev=5)
    async_db_session.add(run)
    await async_db_session.commit()
    run_id = run.id

    with pytest.raises(tasks.BodyRevisionMissingError):
        await tasks._extract_claims_async("task-1", run_id)

    async_db_session.expunge_all()
    refreshed = await tasks.load_run(async_db_session, run_id)
    assert refreshed.status == "failed"
    assert refreshed.error_code == "body_not_found"


async def test_unknown_run_id_raises(use_test_session, fake_provider):
    """run 不存在时抛错，让 chain 中断（原实现返回 error dict，chain 继续跑）。"""
    with pytest.raises(tasks.RunNotFoundError):
        await tasks._extract_claims_async("task-1", 999999)


# --- 失败必须抛异常，且失败后不得继续 -----------------------------------------


async def test_provider_failure_raises_and_marks_run_failed(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """回归：返回 {"status": "error"} 会被 Celery 当成成功，chain 继续执行。"""
    fake_provider.extract_error = httpx.ConnectError("gateway down")

    with pytest.raises(httpx.ConnectError):
        await tasks._extract_claims_async("task-1", pipeline_setup.id)

    async_db_session.expunge_all()
    run = await tasks.load_run(async_db_session, pipeline_setup.id)
    assert run.status == "failed"
    assert run.error_code == "extraction_error"
    assert "gateway down" in run.error_detail


async def test_failed_extraction_persists_no_claims(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    fake_provider.claims = [claim_payload("李长风", "fp_1")]
    fake_provider.extract_error = RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await tasks._extract_claims_async("task-1", pipeline_setup.id)

    assert await load_claims(async_db_session) == []


async def test_failed_run_blocks_summary(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """抽取失败后摘要必须拒绝执行，否则失败的 run 仍会产出摘要。"""
    await tasks.fail_run(
        async_db_session,
        pipeline_setup.id,
        phase="extract",
        error_code="extraction_error",
        error_detail="forced",
    )

    with pytest.raises(tasks.RunFailedError):
        await tasks._generate_summary_async("task-2", pipeline_setup.id)

    summaries = list(
        (await async_db_session.execute(select(DocumentSummary))).scalars().all()
    )
    assert summaries == []


async def test_failed_run_blocks_scan(
    use_test_session, async_db_session, pipeline_setup, fake_provider, make_claim
):
    """抽取失败后扫描必须拒绝执行，否则会基于半套 claim 报告告警。"""
    async_db_session.add(
        make_claim(
            project_id="proj_a",
            chapter_id="ch_a",
            subject_text="李长风",
            predicate="alive",
            object_value="false",
            timeline_id="main",
            story_order=10.0,
            fingerprint="fp_dead",
            status="accepted",
        )
    )
    async_db_session.add(
        make_claim(
            project_id="proj_a",
            chapter_id="ch_a",
            subject_text="李长风",
            predicate="alive",
            object_value="true",
            timeline_id="main",
            story_order=20.0,
            fingerprint="fp_alive",
            status="accepted",
        )
    )
    await async_db_session.commit()
    await tasks.fail_run(
        async_db_session,
        pipeline_setup.id,
        phase="extract",
        error_code="extraction_error",
        error_detail="forced",
    )

    with pytest.raises(tasks.RunFailedError):
        await tasks._scan_rules_async("task-3", pipeline_setup.id)

    issues = list((await async_db_session.execute(select(GuardIssue))).scalars().all())
    assert issues == [], "冲突确实存在，但 run 已失败，不得产出告警"


async def test_failed_run_keeps_its_original_error_code(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """下游拒绝执行时不覆盖最初的失败原因。"""
    await tasks.fail_run(
        async_db_session,
        pipeline_setup.id,
        phase="extract",
        error_code="extraction_error",
        error_detail="gateway down",
    )

    with pytest.raises(tasks.RunFailedError):
        await tasks._scan_rules_async("task-3", pipeline_setup.id)

    async_db_session.expunge_all()
    run = await tasks.load_run(async_db_session, pipeline_setup.id)
    assert run.error_code == "extraction_error"
    assert run.error_detail == "gateway down"


async def test_summary_failure_raises_and_marks_run_failed(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    fake_provider.summary_error = RuntimeError("summary gateway down")

    with pytest.raises(RuntimeError):
        await tasks._generate_summary_async("task-2", pipeline_setup.id)

    async_db_session.expunge_all()
    run = await tasks.load_run(async_db_session, pipeline_setup.id)
    assert run.status == "failed"
    assert run.error_code == "summary_error"


async def test_scan_failure_raises_and_marks_run_failed(
    use_test_session, async_db_session, pipeline_setup, fake_provider, monkeypatch
):
    class ExplodingScanner:
        def __init__(self, rule_version):
            pass

        async def scan_chapter(self, **kwargs):
            raise RuntimeError("scanner bug")

    monkeypatch.setattr(tasks, "RuleScanner", ExplodingScanner)

    with pytest.raises(RuntimeError):
        await tasks._scan_rules_async("task-3", pipeline_setup.id)

    async_db_session.expunge_all()
    run = await tasks.load_run(async_db_session, pipeline_setup.id)
    assert run.status == "failed"
    assert run.error_code == "scan_error"


# --- claim 落库、字段完整性与作废 ---------------------------------------------


async def test_claims_are_persisted_with_run_revision(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    fake_provider.claims = [
        claim_payload("李长风", "fp_1"),
        claim_payload("王二", "fp_2", object_value="false"),
    ]

    result = await tasks._extract_claims_async("task-1", pipeline_setup.id)

    assert result["claims_count"] == 2
    claims = await load_claims(async_db_session)
    assert len(claims) == 2
    assert {claim.subject_text for claim in claims} == {"李长风", "王二"}
    assert all(claim.chapter_id == "ch_a" for claim in claims)
    assert all(claim.body_rev == 1 for claim in claims)
    assert all(claim.source_kind == "body" for claim in claims)
    assert all(claim.status == "accepted" for claim in claims)


async def test_story_order_comes_from_the_absolute_anchor(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """正文写明时间时，story_order 由时间线服务按锚点算出并落库。

    值本身就是那个绝对时间 —— 标尺来自正文，因此跨块跨章可比。
    """
    fake_provider.claims = [
        ordered_payload("李长风", "fp_1", 10.0),
        ordered_payload("王二", "fp_2", 20.0),
    ]

    await tasks._extract_claims_async("task-1", pipeline_setup.id)

    claims = await load_claims(async_db_session)
    assert [float(claim.story_order) for claim in claims] == [
        expected_order(10.0),
        expected_order(20.0),
    ]
    assert all(claim.timeline_id == "main" for claim in claims)


async def test_model_supplied_story_order_is_overwritten_by_the_anchor(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """模型自行编的序号不会被采信 —— 它只在块内有意义。

    回归的是「换一种方式伪造顺序」：抽取逐块进行，模型给的 99.0 与别的块给的
    数字之间没有共同标尺，而规则扫描是全项目范围的。
    """
    fake_provider.claims = [ordered_payload("李长风", "fp_1", 10.0, story_order=99.0)]

    await tasks._extract_claims_async("task-1", pipeline_setup.id)

    claim = (await load_claims(async_db_session))[0]
    assert float(claim.story_order) == expected_order(10.0)
    assert float(claim.story_order) != 99.0


async def test_local_narration_order_never_becomes_a_global_order(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """块内序号不会升格成全局序号：两条 narration_local claim 都拿不到顺序。

    模型在块 0 里给了 2.0、在块 5 里给了 1.0，各自块内自洽。直接落库就会让规则
    得出「块 5 的事件更早」这种凭空结论。
    """
    fake_provider.claims = [
        ordered_payload("李长风", "fp_chunk0", 2.0, order_basis="narration_local",
                        temporal_anchor_value=None, story_order=2.0),
        ordered_payload("陆青", "fp_chunk5", 1.0, order_basis="narration_local",
                        temporal_anchor_value=None, story_order=1.0),
    ]

    result = await tasks._extract_claims_async("task-1", pipeline_setup.id)

    claims = await load_claims(async_db_session)
    assert len(claims) == 2, "顺序不可用不影响落库"
    assert all(claim.story_order is None for claim in claims)
    assert result["pending_order_claims"] == 2


async def test_relative_anchor_yields_no_order_but_keeps_the_claim(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """MVP 限制：只有绝对时间能定位。相对表述保持 NULL，进待确认列表。"""
    fake_provider.claims = [
        ordered_payload(
            "李长风",
            "fp_rel",
            5.0,
            order_basis="relative_to_anchor",
            temporal_anchor_text="三日后",
            temporal_anchor_value=None,
            temporal_relation="after",
            temporal_relation_ref="李长风下山",
        )
    ]

    result = await tasks._extract_claims_async("task-1", pipeline_setup.id)

    claim = (await load_claims(async_db_session))[0]
    assert claim.story_order is None
    assert claim.timeline_id == "main", "线仍然记下来，只是没有顺序"
    assert result["pending_order_claims"] == 1


async def test_unknown_order_stays_null_and_claim_still_lands(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """架构 4.3：无法可靠确定顺序时保持 NULL，claim 仍然落库进待确认。

    回归的是「为了让规则命中而伪造顺序」：早期实现把 Chapter.idx 压成
    story_order，正常倒叙会被判成时序矛盾。
    """
    fake_provider.claims = [
        claim_payload("李长风", "fp_1"),
        claim_payload("王二", "fp_2"),
    ]

    result = await tasks._extract_claims_async("task-1", pipeline_setup.id)

    claims = await load_claims(async_db_session)
    assert len(claims) == 2, "顺序不可靠不影响落库"
    assert all(claim.story_order is None for claim in claims)
    assert all(claim.valid_from_order is None for claim in claims)
    assert all(claim.valid_to_order is None for claim in claims)
    assert all(claim.timeline_id is None for claim in claims), "不兜底填 main"
    assert result["pending_order_claims"] == 2


async def test_chapter_idx_is_never_used_as_story_order(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """回归锚点：story_order 不得来自 Chapter.idx。

    seed_project 给 ch_a 的 idx 是 1024。早期实现会把顺序压进 (1024, 1025)
    区间；现在没有可靠依据就是 NULL，不存在任何与章节序号相关的值。
    """
    fake_provider.claims = [claim_payload(f"角色{i}", f"fp_{i}") for i in range(5)]

    await tasks._extract_claims_async("task-1", pipeline_setup.id)

    chapter_idx = (
        await async_db_session.execute(select(Chapter.idx).where(Chapter.id == "ch_a"))
    ).scalar_one()
    claims = await load_claims(async_db_session)
    assert len(claims) == 5
    for claim in claims:
        assert claim.story_order is None, (
            f"story_order={claim.story_order} 被伪造了；章节 idx={chapter_idx}"
        )


async def test_unreliable_basis_drops_the_model_supplied_order(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """块内顺序 / 低置信度 / 缺依据都不采信，即使模型给了 story_order 数值。"""
    fake_provider.claims = [
        ordered_payload("块内者", "fp_local", 5.0, order_basis="narration_local",
                        story_order=5.0),
        ordered_payload("模糊者", "fp_unsure", 6.0, order_confidence=0.3, story_order=6.0),
        claim_payload("无依据者", "fp_nobasis", timeline_id="main", story_order=7.0),
    ]

    result = await tasks._extract_claims_async("task-1", pipeline_setup.id)

    claims = await load_claims(async_db_session)
    assert len(claims) == 3
    assert all(claim.story_order is None for claim in claims)
    assert result["pending_order_claims"] == 3


async def test_order_without_timeline_is_not_closed_into_intervals(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """timeline_id 未知时不闭合区间：叙事线不明就不能判定谁在谁之前。"""
    fake_provider.claims = [
        ordered_payload("李长风", "fp_1", 10.0, timeline_id=None, predicate="alive"),
        ordered_payload("李长风", "fp_2", 20.0, timeline_id=None, predicate="alive",
                        object_value="false"),
    ]

    await tasks._extract_claims_async("task-1", pipeline_setup.id)

    claims = await load_claims(async_db_session)
    assert all(claim.valid_to_order is None for claim in claims), "无时间线不闭合"


async def test_stateful_predicate_intervals_are_closed(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """顺序可靠且同时间线时，状态型谓词的有效区间前后闭合，最后一条保持开区间。

    不闭合 valid_to_order，ownership 规则的区间重叠判定就永远成立（恒真误报）。
    """
    fake_provider.claims = [
        ordered_payload("李长风", "fp_1", 10.0, predicate="alive", object_value="true"),
        ordered_payload("李长风", "fp_2", 20.0, predicate="alive", object_value="false"),
    ]

    await tasks._extract_claims_async("task-1", pipeline_setup.id)

    first, second = await load_claims(async_db_session)
    assert float(first.valid_from_order) == expected_order(10.0)
    assert float(first.valid_to_order) == expected_order(20.0), "前一条被后一条闭合"
    assert float(second.valid_from_order) == expected_order(20.0)
    assert second.valid_to_order is None, "最后一条一直有效"


async def test_intervals_are_not_closed_across_timelines(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """架构 4.3：多线叙事先按 timeline_id 隔离，无跨线锚点就不比较。"""
    fake_provider.claims = [
        ordered_payload("李长风", "fp_a", 10.0, timeline_id="thread_a", predicate="alive"),
        ordered_payload("李长风", "fp_b", 20.0, timeline_id="thread_b", predicate="alive",
                        object_value="false"),
    ]

    await tasks._extract_claims_async("task-1", pipeline_setup.id)

    claims = await load_claims(async_db_session)
    assert all(claim.valid_to_order is None for claim in claims), "跨线不互相闭合"


async def test_distinct_owned_items_do_not_close_each_others_intervals(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """同时拥有剑和盾并不冲突，闭合区间必须按对象分组。"""
    fake_provider.claims = [
        ordered_payload("李长风", "fp_sword", 10.0, predicate="owns",
                        object_type="entity", object_value="长剑"),
        ordered_payload("李长风", "fp_shield", 20.0, predicate="owns",
                        object_type="entity", object_value="铁盾"),
    ]

    await tasks._extract_claims_async("task-1", pipeline_setup.id)

    claims = await load_claims(async_db_session)
    assert all(claim.valid_to_order is None for claim in claims), "不同物品互不终止"


async def test_provider_supplied_positions_are_preserved(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """模型自己算好的区间沿用，不被覆盖；story_order 仍由锚点决定。"""
    fake_provider.claims = [
        ordered_payload(
            "李长风",
            "fp_1",
            3.0,
            timeline_id="thread_b",
            valid_from_order=expected_order(3.0),
            valid_to_order=expected_order(9.0),
        )
    ]

    await tasks._extract_claims_async("task-1", pipeline_setup.id)

    claim = (await load_claims(async_db_session))[0]
    assert claim.timeline_id == "thread_b"
    assert float(claim.story_order) == expected_order(3.0)
    assert float(claim.valid_to_order) == expected_order(9.0)


async def test_subject_entry_id_is_resolved_and_persisted(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """subject_entry_id 必须落库：规则按 entry_id 分组，NULL 等于关掉跨章检测。"""
    from db.models_codex import CodexAlias, CodexEntry

    async_db_session.add(
        CodexEntry(
            id="cx_lee",
            project_id="proj_a",
            kind="character",
            name="李长风",
            description="主角",
            attrs={},
        )
    )
    await async_db_session.flush()
    async_db_session.add(CodexAlias(entry_id="cx_lee", alias="李长风"))
    await async_db_session.flush()

    fake_provider.claims = [claim_payload("李长风", "fp_1")]

    await tasks._extract_claims_async("task-1", pipeline_setup.id)

    claim = (await load_claims(async_db_session))[0]
    assert claim.subject_entry_id == "cx_lee"


async def test_object_entry_id_is_resolved_and_persisted(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """object_entry_id 必须落库：ownership 规则按被拥有物的 entry_id 分组。"""
    from db.models_codex import CodexAlias, CodexEntry

    async_db_session.add(
        CodexEntry(
            id="cx_sword",
            project_id="proj_a",
            kind="item",
            name="长剑",
            description="主角的佩剑",
            attrs={},
        )
    )
    await async_db_session.flush()
    async_db_session.add(CodexAlias(entry_id="cx_sword", alias="长剑"))
    await async_db_session.flush()

    fake_provider.claims = [
        claim_payload(
            "李长风", "fp_1", predicate="owns", object_type="entity", object_value="长剑"
        )
    ]

    await tasks._extract_claims_async("task-1", pipeline_setup.id)

    claim = (await load_claims(async_db_session))[0]
    assert claim.object_entry_id == "cx_sword"


async def test_new_revision_supersedes_previous_claims(
    use_test_session, async_db_session, pipeline_setup, fake_provider, make_run
):
    """新版本发布时旧版本 claim 必须作废，否则新旧共存会误报冲突。"""
    fake_provider.claims = [claim_payload("李长风", "fp_1")]
    await tasks._extract_claims_async("task-1", pipeline_setup.id)

    await add_version(async_db_session, rev=2, html="<p>第二版正文</p>")
    run2 = make_run(project_id="proj_a", chapter_id="ch_a", body_rev=2)
    async_db_session.add(run2)
    await async_db_session.flush()

    fake_provider.claims = [claim_payload("李长风", "fp_1_rev2", object_value="false")]
    result = await tasks._extract_claims_async("task-2", run2.id)

    assert result["superseded_claims"] == 1
    claims = await load_claims(async_db_session)
    assert [(claim.body_rev, claim.status) for claim in claims] == [
        (1, "superseded"),
        (2, "accepted"),
    ]


async def test_replaying_the_same_revision_updates_in_place(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """同版本重放原地更新已有行，**不删除**任何行。

    uq_claim_body_source 是 (chapter_id, body_rev, fingerprint, extractor_version)
    上的部分唯一索引，不含 status —— 所以重放不能盲插。早期实现在这里 DELETE 掉
    上一轮的行，但架构 4.3 要求「旧正文版本的 claim 不删除，标为 superseded，
    确保告警可追溯」，而且 GuardIssueEvidence.claim_id 是 ON DELETE SET NULL，
    删 claim 会把已有告警的证据指针悄悄清空。
    """
    fake_provider.claims = [claim_payload("李长风", "fp_1")]
    await tasks._extract_claims_async("task-1", pipeline_setup.id)
    original_id = (await load_claims(async_db_session))[0].id

    result = await tasks._extract_claims_async("task-1-retry", pipeline_setup.id)

    assert result["updated_claims"] == 1, "已有指纹应当被原地更新"
    assert result["inserted_claims"] == 0
    claims = await load_claims(async_db_session)
    assert len(claims) == 1, "同 (body_rev, fingerprint) 只剩一行"
    assert claims[0].id == original_id, "行没有被删掉重建 —— 证据指针必须保持有效"
    assert claims[0].status == "accepted"


# --- 同版本候选集替换：集合 diff ----------------------------------------------
#
# 一次抽取产出的是「这一版正文里的全部事实」，是一个集合。重放必须让库里的集合
# 等于本次的集合，否则留下的是两次运行的并集：作者删掉的那句话，告警还在报。


async def test_a_claim_missing_from_the_replay_is_superseded(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """A/B -> 仅 A：B 不再出现，必须退出当前有效集合。

    早期的「跳过已有指纹」策略在这里什么都不做，B 一直挂着 accepted 参与规则 ——
    作者已经把那句话删了，告警却还在报，而且怎么改都消不掉。
    """
    fake_provider.claims = [
        claim_payload("李长风", "fp_a"),
        claim_payload("王二", "fp_b"),
    ]
    await tasks._extract_claims_async("task-1", pipeline_setup.id)
    ids_before = {claim.fingerprint: claim.id for claim in await load_claims(async_db_session)}

    fake_provider.claims = [claim_payload("李长风", "fp_a")]
    result = await tasks._extract_claims_async("task-2", pipeline_setup.id)

    assert result["dropped_claims"] == 1
    assert result["updated_claims"] == 1
    assert result["inserted_claims"] == 0

    claims = {claim.fingerprint: claim for claim in await load_claims(async_db_session)}
    assert set(claims) == {"fp_a", "fp_b"}, "缺失的 claim 被删掉了，可追溯性没了"
    assert claims["fp_a"].status == "accepted"
    assert claims["fp_b"].status == "superseded"
    assert {fp: claim.id for fp, claim in claims.items()} == ids_before, "行 id 必须稳定"


async def test_a_changed_claim_is_refreshed_in_place(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """同一 fingerprint 的重算结果必须刷新：entry_id、timeline、order、confidence。

    跳过已有指纹时这些字段永远停在第一次运行的值上：作者事后建了词条，claim 的
    subject_entry_id 还是空的 —— 按 entry_id 分组的跨章节规则永远看不到它。
    """
    from db.models_codex import CodexAlias, CodexEntry

    fake_provider.claims = [
        ordered_payload("李长风", "fp_a", 10.0, timeline_id="line_a", confidence=0.6)
    ]
    await tasks._extract_claims_async("task-1", pipeline_setup.id)

    first = (await load_claims(async_db_session))[0]
    original_id = first.id
    assert first.subject_entry_id is None, "前置条件：此时还没有词条可链接"
    assert float(first.confidence) == pytest.approx(0.6)
    assert float(first.story_order) == pytest.approx(expected_order(10.0))

    # 作者补建了词条，正文时间也被改写 —— 重放必须把这些都刷进去
    async_db_session.add(
        CodexEntry(
            id="cx_lee", project_id="proj_a", kind="character", name="李长风",
            description="主角", attrs={},
        )
    )
    await async_db_session.flush()
    async_db_session.add(CodexAlias(entry_id="cx_lee", alias="李长风"))
    await async_db_session.commit()

    fake_provider.claims = [
        ordered_payload("李长风", "fp_a", 99.0, timeline_id="line_b", confidence=0.95)
    ]
    result = await tasks._extract_claims_async("task-2", pipeline_setup.id)

    assert result["updated_claims"] == 1
    assert result["inserted_claims"] == 0
    claims = await load_claims(async_db_session)
    assert len(claims) == 1
    refreshed = claims[0]
    assert refreshed.id == original_id, "刷新必须原地进行，证据指针才不会失效"
    assert refreshed.subject_entry_id == "cx_lee"
    assert refreshed.timeline_id == "line_b"
    assert float(refreshed.story_order) == pytest.approx(expected_order(99.0))
    assert float(refreshed.confidence) == pytest.approx(0.95)
    assert refreshed.status == "accepted"


async def test_a_claim_that_disappears_and_returns_is_restored(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """A 消失后再次出现：同一行恢复成 accepted，不是新插一行。

    唯一键不含 status，superseded 的行仍然占着键位 —— 盲插会直接撞唯一键。
    """
    fake_provider.claims = [
        claim_payload("李长风", "fp_a"),
        claim_payload("王二", "fp_b"),
    ]
    await tasks._extract_claims_async("task-1", pipeline_setup.id)
    ids_before = {claim.fingerprint: claim.id for claim in await load_claims(async_db_session)}

    fake_provider.claims = [claim_payload("李长风", "fp_a")]
    await tasks._extract_claims_async("task-2", pipeline_setup.id)

    fake_provider.claims = [
        claim_payload("李长风", "fp_a"),
        claim_payload("王二", "fp_b"),
    ]
    result = await tasks._extract_claims_async("task-3", pipeline_setup.id)

    assert result["restored_claims"] == 1
    assert result["inserted_claims"] == 0, "复现的 claim 被当成新行插入了"
    claims = {claim.fingerprint: claim for claim in await load_claims(async_db_session)}
    assert len(claims) == 2, "同 fingerprint 出现了重复行"
    assert claims["fp_b"].status == "accepted"
    assert {fp: claim.id for fp, claim in claims.items()} == ids_before


async def test_evidence_survives_a_claim_leaving_the_set(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """claim 退出当前集合时，已有告警的证据指针必须还指着它。

    这正是「删掉再重插」会毁掉的东西：GuardIssueEvidence.claim_id 是
    ON DELETE SET NULL，删 claim 不报错，只让作者看到一条没有出处的告警。
    """
    from db.models_consistency_extended import GuardIssueEvidence

    fake_provider.claims = [
        ordered_payload("李长风", "fp_dead", 10.0, object_value="false"),
        ordered_payload("李长风", "fp_alive", 20.0, object_value="true"),
    ]
    await tasks._extract_claims_async("task-1", pipeline_setup.id)
    await tasks._scan_rules_async("task-scan", pipeline_setup.id)

    evidence_before = list(
        (await async_db_session.execute(select(GuardIssueEvidence))).scalars().all()
    )
    assert evidence_before, "前置条件：扫描必须写出证据行"
    claim_ids_before = {row.claim_id for row in evidence_before}
    assert None not in claim_ids_before

    # 两条都从本次结果里消失 —— 集合替换会把它们标 superseded
    fake_provider.claims = [claim_payload("新事实", "fp_other")]
    result = await tasks._extract_claims_async("task-2", pipeline_setup.id)
    assert result["dropped_claims"] == 2

    async_db_session.expunge_all()
    evidence_after = list(
        (await async_db_session.execute(select(GuardIssueEvidence))).scalars().all()
    )
    assert {row.claim_id for row in evidence_after} == claim_ids_before
    survivors = {claim.id: claim for claim in await load_claims(async_db_session)}
    for claim_id in claim_ids_before:
        assert claim_id in survivors, "被证据引用的 claim 行不见了"
        assert survivors[claim_id].status == "superseded"


async def test_an_author_rejection_is_not_overwritten_by_a_replay(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """status='rejected' 是人的判断，重放不得把它改回 accepted 或降级成 superseded。

    否则作者每否掉一条，下一次重跑管道就把它原样退回来，告警列表永远清不干净。
    """
    fake_provider.claims = [
        claim_payload("李长风", "fp_a"),
        claim_payload("王二", "fp_b"),
    ]
    await tasks._extract_claims_async("task-1", pipeline_setup.id)

    claims = {claim.fingerprint: claim for claim in await load_claims(async_db_session)}
    rejected_id = claims["fp_a"].id
    dropped_id = claims["fp_b"].id
    for fingerprint in ("fp_a", "fp_b"):
        claims[fingerprint].status = "rejected"
    async_db_session.add_all(list(claims.values()))
    await async_db_session.commit()

    # fp_a 本次仍然抽到，fp_b 消失了；两条都不该被动状态
    fake_provider.claims = [claim_payload("李长风", "fp_a")]
    result = await tasks._extract_claims_async("task-2", pipeline_setup.id)

    assert result["kept_rejected_claims"] == 2
    assert result["dropped_claims"] == 0
    after = {claim.id: claim for claim in await load_claims(async_db_session)}
    assert after[rejected_id].status == "rejected"
    assert after[dropped_id].status == "rejected"


async def test_replay_preserves_guard_issue_evidence_pointers(
    use_test_session, async_db_session, pipeline_setup, fake_provider, make_run
):
    """重放不能让已有告警变成「没有出处的告警」。

    GuardIssueEvidence.claim_id 是 ON DELETE SET NULL 的外键：删 claim 不会报错，
    只会把证据指针静默置空。这条测试直接盯住架构 4.3 保护的可追溯性。
    """
    from db.models_consistency_extended import GuardIssueEvidence

    fake_provider.claims = [
        ordered_payload("李长风", "fp_dead", 10.0, object_value="false"),
        ordered_payload("李长风", "fp_alive", 20.0, object_value="true"),
    ]
    await tasks._extract_claims_async("task-1", pipeline_setup.id)
    await tasks._scan_rules_async("task-scan", pipeline_setup.id)

    evidence_before = list(
        (await async_db_session.execute(select(GuardIssueEvidence))).scalars().all()
    )
    assert evidence_before, "前置条件：扫描必须写出证据行"
    claim_ids_before = {row.claim_id for row in evidence_before}
    assert None not in claim_ids_before

    # 同版本重放（重试）
    await tasks._extract_claims_async("task-1-retry", pipeline_setup.id)

    async_db_session.expunge_all()
    evidence_after = list(
        (await async_db_session.execute(select(GuardIssueEvidence))).scalars().all()
    )
    claim_ids_after = {row.claim_id for row in evidence_after}
    assert None not in claim_ids_after, "重放把证据的 claim_id 置空了（claim 被删了）"
    assert claim_ids_after == claim_ids_before


async def test_superseded_claims_are_never_deleted(
    use_test_session, async_db_session, pipeline_setup, fake_provider, make_run
):
    """架构 4.3：旧正文版本的 claim 只标 superseded，行必须留着。"""
    fake_provider.claims = [claim_payload("李长风", "fp_v1")]
    await tasks._extract_claims_async("task-1", pipeline_setup.id)
    old_id = (await load_claims(async_db_session))[0].id

    await add_version(async_db_session, rev=2, html="<p>第二版</p>")
    run2 = make_run(project_id="proj_a", chapter_id="ch_a", body_rev=2)
    async_db_session.add(run2)
    await async_db_session.commit()

    fake_provider.claims = [claim_payload("李长风", "fp_v2")]
    await tasks._extract_claims_async("task-2", run2.id)
    # 新版本上再重放一次，确认旧行也不会被顺手清掉
    await tasks._extract_claims_async("task-2-retry", run2.id)

    claims = await load_claims(async_db_session)
    by_id = {claim.id: claim for claim in claims}
    assert old_id in by_id, "旧版本的 claim 行被删除了"
    assert by_id[old_id].status == "superseded"
    assert by_id[old_id].body_rev == 1


async def test_replay_leaves_no_row_that_would_violate_the_unique_index(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """重放三次后，(body_rev, fingerprint, extractor_version) 依然唯一。

    在 PostgreSQL 上重复的行会被部分唯一索引直接拒绝；SQLite 上索引被剔除，
    所以这里显式断言重复计数，等价地覆盖同一条约束。
    """
    fake_provider.claims = [
        claim_payload("李长风", "fp_1"),
        claim_payload("王二", "fp_2"),
    ]
    for attempt in range(3):
        await tasks._extract_claims_async(f"task-{attempt}", pipeline_setup.id)

    claims = await load_claims(async_db_session)
    keys = [
        (claim.chapter_id, claim.body_rev, claim.fingerprint, claim.extractor_version)
        for claim in claims
    ]
    assert len(keys) == len(set(keys)), f"重放产生了重复唯一键: {keys}"
    assert len(claims) == 2


async def test_replay_after_supersede_does_not_resurrect_old_rows(
    use_test_session, async_db_session, pipeline_setup, fake_provider, make_run
):
    """旧版本被作废后，同章新版本重放不会把旧行改回 accepted。"""
    fake_provider.claims = [claim_payload("李长风", "fp_1")]
    await tasks._extract_claims_async("task-1", pipeline_setup.id)

    await add_version(async_db_session, rev=2, html="<p>v2</p>")
    run2 = make_run(project_id="proj_a", chapter_id="ch_a", body_rev=2)
    async_db_session.add(run2)
    await async_db_session.flush()
    fake_provider.claims = [claim_payload("李长风", "fp_1")]
    await tasks._extract_claims_async("task-2", run2.id)
    await tasks._extract_claims_async("task-2-retry", run2.id)

    claims = await load_claims(async_db_session)
    by_rev = {claim.body_rev: claim.status for claim in claims}
    assert by_rev == {1: "superseded", 2: "accepted"}


async def test_superseding_is_scoped_to_the_chapter(
    use_test_session,
    async_db_session,
    make_claim,
    pipeline_setup,
    fake_provider,
    make_run,
):
    """作废只针对本章，别的章节的 claim 不受影响。"""
    async_db_session.add(
        Chapter(
            id="ch_b",
            project_id="proj_a",
            volume_id=None,
            title="第二章",
            idx=2048,
            words=0,
            outline=[],
        )
    )
    await async_db_session.flush()
    async_db_session.add(
        make_claim(
            project_id="proj_a",
            chapter_id="ch_b",
            body_rev=1,
            subject_text="别章角色",
            fingerprint="fp_other",
            status="accepted",
        )
    )
    await async_db_session.flush()

    await add_version(async_db_session, rev=2, html="<p>第二版</p>")
    run2 = make_run(project_id="proj_a", chapter_id="ch_a", body_rev=2)
    async_db_session.add(run2)
    await async_db_session.flush()
    fake_provider.claims = [claim_payload("李长风", "fp_new")]

    await tasks._extract_claims_async("task-2", run2.id)

    async_db_session.expunge_all()
    refreshed = (
        await async_db_session.execute(
            select(ConsistencyClaim).where(ConsistencyClaim.fingerprint == "fp_other")
        )
    ).scalar_one()
    assert refreshed.status == "accepted"


async def test_extraction_aborts_if_a_newer_revision_lands_mid_flight(
    use_test_session, async_db_session, pipeline_setup, fake_provider, monkeypatch
):
    """抽取过程中作者又存了一版 → 结论不落库，run 标记 stale_revision。"""
    run_id = pipeline_setup.id

    class RacingProvider:
        extractor_version = "1.0.0"

        async def extract_claims(self, *, content_html, project_id, chapter_id):
            await add_version(async_db_session, rev=2, html="<p>抽取途中的新版</p>")
            return [claim_payload("李长风", "fp_1")]

        async def generate_summary(self, *, content_html, summary_type="chapter"):
            return ("摘要", 1)

    monkeypatch.setattr(tasks, "ConsistencyProvider", RacingProvider)

    with pytest.raises(tasks.StaleRevisionError):
        await tasks._extract_claims_async("task-1", run_id)

    assert await load_claims(async_db_session) == [], "旧 worker 的过期结论不得落库"
    run = await tasks.load_run(async_db_session, run_id)
    assert run.status == "failed"
    assert run.error_code == "stale_revision"


async def test_stale_extraction_does_not_commit_the_supersede(
    use_test_session,
    async_db_session,
    pipeline_setup,
    fake_provider,
    make_claim,
    monkeypatch,
):
    """回归：stale 之前不 rollback，会把已作废的旧 claim 一起提交。

    旧 worker 已经把上一版 claim 标成 superseded；如果直接提交失败标记而不回滚，
    这次作废就生效了 —— 上一版结论凭空消失，而新版本的 claim 还没写进来。
    """
    async_db_session.add(
        make_claim(
            project_id="proj_a",
            chapter_id="ch_a",
            body_rev=1,
            subject_text="上一版角色",
            fingerprint="fp_previous",
            status="accepted",
        )
    )
    await async_db_session.flush()
    await add_version(async_db_session, rev=2, html="<p>v2</p>")
    await async_db_session.commit()

    from db.models_consistency_extended import ConsistencyRun

    run2 = ConsistencyRun(
        project_id="proj_a",
        chapter_id="ch_a",
        body_rev=2,
        pipeline_version=PIPELINE_VERSION,
        status="pending",
        trigger="body_save",
    )
    async_db_session.add(run2)
    await async_db_session.commit()

    class RacingProvider:
        extractor_version = "1.0.0"

        async def extract_claims(self, *, content_html, project_id, chapter_id):
            # rev 2 的 worker 干活期间，作者又存了 rev 3
            await add_version(async_db_session, rev=3, html="<p>v3</p>")
            return [claim_payload("新角色", "fp_new")]

        async def generate_summary(self, *, content_html, summary_type="chapter"):
            return ("摘要", 1)

    monkeypatch.setattr(tasks, "ConsistencyProvider", RacingProvider)

    with pytest.raises(tasks.StaleRevisionError):
        await tasks._extract_claims_async("task-2", run2.id)

    async_db_session.expunge_all()
    previous = (
        await async_db_session.execute(
            select(ConsistencyClaim).where(ConsistencyClaim.fingerprint == "fp_previous")
        )
    ).scalar_one()
    assert previous.status == "accepted", "过期 worker 的作废操作必须被回滚"


# --- 摘要 ---------------------------------------------------------------------


async def test_summary_is_persisted_with_configured_model(
    use_test_session, async_db_session, pipeline_setup, fake_provider, monkeypatch
):
    """model_id 来自配置，不是硬编码的 gpt-4o-mini。"""
    monkeypatch.setattr(settings, "consistency_summary_model", "custom-summary-model")
    fake_provider.summary = ("这一章的摘要", 88)

    await tasks._generate_summary_async("task-2", pipeline_setup.id)

    summary = (await async_db_session.execute(select(DocumentSummary))).scalar_one()
    assert summary.content == "这一章的摘要"
    assert summary.model_id == "custom-summary-model"
    assert summary.token_count == 88
    assert summary.status == "active"
    assert summary.source_rev == 1
    assert summary.owner_type == "chapter"
    assert summary.owner_id == "ch_a"


async def test_new_revision_supersedes_older_active_summary(
    use_test_session, async_db_session, pipeline_setup, fake_provider, make_run
):
    fake_provider.summary = ("第一版摘要", 10)
    await tasks._generate_summary_async("task-2", pipeline_setup.id)

    await add_version(async_db_session, rev=2, html="<p>第二版</p>")
    run2 = make_run(project_id="proj_a", chapter_id="ch_a", body_rev=2)
    async_db_session.add(run2)
    await async_db_session.flush()
    fake_provider.summary = ("第二版摘要", 20)

    await tasks._generate_summary_async("task-3", run2.id)

    async_db_session.expunge_all()
    summaries = list(
        (
            await async_db_session.execute(
                select(DocumentSummary).order_by(DocumentSummary.source_rev)
            )
        )
        .scalars()
        .all()
    )
    assert [(s.source_rev, s.status) for s in summaries] == [
        (1, "superseded"),
        (2, "active"),
    ]


async def test_only_one_active_summary_per_chapter(
    use_test_session, async_db_session, pipeline_setup, fake_provider, make_run
):
    fake_provider.summary = ("v1", 1)
    await tasks._generate_summary_async("task-2", pipeline_setup.id)
    await add_version(async_db_session, rev=2, html="<p>v2</p>")
    run2 = make_run(project_id="proj_a", chapter_id="ch_a", body_rev=2)
    async_db_session.add(run2)
    await async_db_session.flush()
    fake_provider.summary = ("v2", 2)
    await tasks._generate_summary_async("task-3", run2.id)

    async_db_session.expunge_all()
    active = list(
        (
            await async_db_session.execute(
                select(DocumentSummary).where(DocumentSummary.status == "active")
            )
        )
        .scalars()
        .all()
    )
    assert len(active) == 1
    assert active[0].content == "v2"


async def test_late_summary_does_not_overwrite_a_newer_active_summary(
    use_test_session, async_db_session, pipeline_setup, fake_provider, make_run
):
    """旧版本的摘要任务迟到时不得把有效摘要退回旧内容。"""
    await add_version(async_db_session, rev=2, html="<p>v2</p>")
    run2 = make_run(project_id="proj_a", chapter_id="ch_a", body_rev=2)
    async_db_session.add(run2)
    await async_db_session.flush()
    fake_provider.summary = ("新版摘要", 20)
    await tasks._generate_summary_async("task-3", run2.id)

    # rev 1 的摘要任务现在才跑完
    fake_provider.summary = ("旧版摘要", 10)
    with pytest.raises(tasks.StaleRevisionError):
        await tasks._generate_summary_async("task-2", pipeline_setup.id)

    async_db_session.expunge_all()
    active = list(
        (
            await async_db_session.execute(
                select(DocumentSummary).where(DocumentSummary.status == "active")
            )
        )
        .scalars()
        .all()
    )
    assert [s.content for s in active] == ["新版摘要"]


async def test_late_summary_leaves_no_partial_row(
    use_test_session, async_db_session, pipeline_setup, fake_provider, make_run
):
    """迟到的旧版本摘要不留任何行 —— 判定发生在写入之前，且失败前回滚。"""
    await add_version(async_db_session, rev=2, html="<p>v2</p>")
    run2 = make_run(project_id="proj_a", chapter_id="ch_a", body_rev=2)
    async_db_session.add(run2)
    await async_db_session.flush()
    fake_provider.summary = ("新版摘要", 20)
    await tasks._generate_summary_async("task-3", run2.id)

    fake_provider.summary = ("旧版摘要", 10)
    with pytest.raises(tasks.StaleRevisionError):
        await tasks._generate_summary_async("task-2", pipeline_setup.id)

    async_db_session.expunge_all()
    summaries = list(
        (await async_db_session.execute(select(DocumentSummary))).scalars().all()
    )
    assert [s.source_rev for s in summaries] == [2], "不留 rev 1 的半成品行"


async def test_rerunning_summary_for_same_revision_updates_in_place(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """同版本重跑走原地更新，否则会撞 uq_document_summary_key。"""
    fake_provider.summary = ("第一次", 1)
    await tasks._generate_summary_async("task-2", pipeline_setup.id)
    fake_provider.summary = ("第二次", 2)
    await tasks._generate_summary_async("task-2-retry", pipeline_setup.id)

    async_db_session.expunge_all()
    summaries = list(
        (await async_db_session.execute(select(DocumentSummary))).scalars().all()
    )
    assert len(summaries) == 1
    assert summaries[0].content == "第二次"


# --- 扫描 ---------------------------------------------------------------------


async def test_scan_alone_does_not_complete_the_run(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """扫描成功但摘要还没跑：绝不能写 completed / finished_at。

    回归：原实现让扫描支线独占 status，扫描一成功就 completed —— 上下文构建方据此
    去读一份根本不存在的摘要。
    """
    result = await tasks._scan_rules_async("task-3", pipeline_setup.id)

    assert result["status"] == "success"
    assert result["run_completed"] is False
    async_db_session.expunge_all()
    run = await tasks.load_run(async_db_session, pipeline_setup.id)
    assert run.scan_state == "succeeded"
    assert run.summary_state == "pending", "摘要支线还没跑"
    assert run.status == "scanning", "扫描完成不等于整个 run 完成"
    assert run.finished_at is None, "摘要未完成就写 finished_at 是假完成"


async def test_scan_finds_conflicts_from_persisted_claims(
    use_test_session, async_db_session, pipeline_setup, fake_provider, make_claim
):
    """扫描看到的是抽取步骤落库的 claim（管道顺序生效）。"""
    async_db_session.add(
        make_claim(
            project_id="proj_a",
            chapter_id="ch_a",
            subject_text="李长风",
            predicate="alive",
            object_value="false",
            timeline_id="main",
            story_order=10.0,
            fingerprint="fp_dead",
            status="accepted",
        )
    )
    async_db_session.add(
        make_claim(
            project_id="proj_a",
            chapter_id="ch_a",
            subject_text="李长风",
            predicate="alive",
            object_value="true",
            timeline_id="main",
            story_order=20.0,
            fingerprint="fp_alive",
            status="accepted",
        )
    )
    await async_db_session.flush()

    result = await tasks._scan_rules_async("task-3", pipeline_setup.id)

    assert result["issues_found"] == 1
    issues = list((await async_db_session.execute(select(GuardIssue))).scalars().all())
    assert issues[0].issue_type == "alive_conflict"


async def test_end_to_end_extraction_feeds_the_scanner(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """模型给出可靠顺序时，抽取落库的字段足以让规则检出冲突。"""
    fake_provider.claims = [
        ordered_payload("李长风", "fp_dead", 10.0, object_value="false"),
        ordered_payload("李长风", "fp_alive", 20.0, object_value="true"),
    ]
    await tasks._extract_claims_async("task-1", pipeline_setup.id)

    result = await tasks._scan_rules_async("task-3", pipeline_setup.id)

    assert result["issues_found"] == 1, "抽取写入的字段必须足够让规则排序判断"
    issues = list((await async_db_session.execute(select(GuardIssue))).scalars().all())
    assert issues[0].issue_type == "alive_conflict"


async def test_unknown_order_produces_no_hard_alert(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """顺序未知的「先死后活」不出硬告警 —— 无法判定先后就不是矛盾。

    架构 4.3：无法可靠确定顺序时只进入待确认列表，不运行依赖时序的硬规则。
    """
    fake_provider.claims = [
        claim_payload("李长风", "fp_dead", object_value="false"),
        claim_payload("李长风", "fp_alive", object_value="true"),
    ]
    await tasks._extract_claims_async("task-1", pipeline_setup.id)

    result = await tasks._scan_rules_async("task-3", pipeline_setup.id)

    assert result["issues_found"] == 0, "顺序未知却出了告警 —— 顺序被伪造了"
    issues = list((await async_db_session.execute(select(GuardIssue))).scalars().all())
    assert issues == []
    assert len(await load_claims(async_db_session)) == 2, "claim 仍在库里等待确认"


async def test_flashback_produces_no_hard_alert(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """倒叙不出硬告警。

    这是伪造顺序最典型的受害场景：正文里先写「李长风已死」，随后倒叙他生前的
    一幕。按叙述位置（章节序号或块内序号）排序会得到「死→活」，判成高等级复活
    矛盾；按故事顺序才是「活→死」，完全正常。倒叙那一幕没有绝对时间可锚定，
    模型只能报 relative_to_anchor，于是 story_order 保持 NULL，规则看不到它们。
    """
    fake_provider.claims = [
        ordered_payload("李长风", "fp_dead", 10.0, object_value="false"),
        ordered_payload("李长风", "fp_alive", 20.0, object_value="true",
                        order_basis="relative_to_anchor",
                        temporal_anchor_text="那一年他还在山上",
                        temporal_anchor_value=None),
    ]
    await tasks._extract_claims_async("task-1", pipeline_setup.id)

    result = await tasks._scan_rules_async("task-3", pipeline_setup.id)

    assert result["issues_found"] == 0, "倒叙被误判成时序矛盾"
    assert len(await load_claims(async_db_session)) == 2


async def test_flashback_with_correct_story_order_is_not_a_conflict(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """模型正确识别倒叙、给出更小的 story_order 时，顺序是「活→死」，没有矛盾。

    这条和上一条互补：上一条验证「不可采信就不判」，这条验证「采信正确顺序时
    结论也是对的」—— 倒叙里叙述在后的那一幕在故事时间里更早。
    """
    fake_provider.claims = [
        # 叙述在前，但故事时间更晚
        ordered_payload("李长风", "fp_dead", 20.0, object_value="false"),
        # 叙述在后（倒叙），故事时间更早 —— 模型用明确时间锚定了它
        ordered_payload("李长风", "fp_alive", 10.0, object_value="true"),
    ]
    await tasks._extract_claims_async("task-1", pipeline_setup.id)

    result = await tasks._scan_rules_async("task-3", pipeline_setup.id)

    assert result["issues_found"] == 0, "按故事顺序是「活→死」，不该出告警"


async def test_cross_timeline_claims_do_not_conflict(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """架构 4.3：不同 timeline_id 的事件不比较，除非有已确认的跨线锚点。"""
    fake_provider.claims = [
        ordered_payload("李长风", "fp_dead", 10.0, object_value="false",
                        timeline_id="thread_a"),
        ordered_payload("李长风", "fp_alive", 20.0, object_value="true",
                        timeline_id="thread_b"),
    ]
    await tasks._extract_claims_async("task-1", pipeline_setup.id)

    result = await tasks._scan_rules_async("task-3", pipeline_setup.id)

    assert result["issues_found"] == 0, "跨时间线被当成同一条时间线比较了"


async def test_uncertain_claim_downgrades_severity(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """架构 7：任一输入 claim 为 uncertain 时不得产生 high severity。"""
    fake_provider.claims = [
        ordered_payload("李长风", "fp_dead", 10.0, object_value="false"),
        ordered_payload("李长风", "fp_alive", 20.0, object_value="true",
                        certainty="uncertain"),
    ]
    await tasks._extract_claims_async("task-1", pipeline_setup.id)

    result = await tasks._scan_rules_async("task-3", pipeline_setup.id)

    assert result["issues_found"] == 1, "仍然提示，只是降级"
    issues = list((await async_db_session.execute(select(GuardIssue))).scalars().all())
    assert issues[0].severity != "high", "uncertain 证据不得出 high"
    assert issues[0].severity == "medium"


async def test_scan_aborts_when_revision_advanced(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    run_id = pipeline_setup.id
    await add_version(async_db_session, rev=2, html="<p>新版</p>")

    with pytest.raises(tasks.StaleRevisionError):
        await tasks._scan_rules_async("task-3", run_id)

    async_db_session.expunge_all()
    run = await tasks.load_run(async_db_session, run_id)
    assert run.status == "failed"
    assert run.error_code == "stale_revision"


async def test_stale_scan_does_not_commit_issues(
    use_test_session, async_db_session, pipeline_setup, fake_provider, make_claim, monkeypatch
):
    """扫描期间版本推进 → 已生成的 issue 必须随事务回滚。"""
    async_db_session.add(
        make_claim(
            project_id="proj_a",
            chapter_id="ch_a",
            subject_text="李长风",
            predicate="alive",
            object_value="false",
            timeline_id="main",
            story_order=10.0,
            fingerprint="fp_dead",
            status="accepted",
        )
    )
    async_db_session.add(
        make_claim(
            project_id="proj_a",
            chapter_id="ch_a",
            subject_text="李长风",
            predicate="alive",
            object_value="true",
            timeline_id="main",
            story_order=20.0,
            fingerprint="fp_alive",
            status="accepted",
        )
    )
    await async_db_session.flush()
    await async_db_session.commit()

    real_scanner_cls = tasks.RuleScanner

    class RacingScanner(real_scanner_cls):
        async def scan_chapter(self, **kwargs):
            issue_ids = await super().scan_chapter(**kwargs)
            # 扫描完成、提交之前作者又存了一版
            await add_version(async_db_session, rev=2, html="<p>扫描途中的新版</p>")
            return issue_ids

    monkeypatch.setattr(tasks, "RuleScanner", RacingScanner)

    with pytest.raises(tasks.StaleRevisionError):
        await tasks._scan_rules_async("task-3", pipeline_setup.id)

    async_db_session.expunge_all()
    issues = list((await async_db_session.execute(select(GuardIssue))).scalars().all())
    assert issues == [], "过期扫描的告警不得落库"


# --- 阶段状态机 ---------------------------------------------------------------
#
# 摘要与扫描并行，一个 status 列表达不了两条支线。这里钉住的是真实数据库行为：
# 两种完成顺序、单支线失败、双失败首错、重复投递幂等 —— 不是断言某个函数常量。


async def phase_states(db, run_id: int) -> dict[str, str]:
    """读回三个阶段列的当前值。"""
    db.expunge_all()
    run = await tasks.load_run(db, run_id)
    return {
        "extract": run.extract_state,
        "summary": run.summary_state,
        "scan": run.scan_state,
    }


async def run_all_three(run_id: int, *, order: tuple[str, ...]) -> dict[str, dict]:
    """按给定顺序跑完三个阶段，返回各阶段的返回值。"""
    runners = {
        "extract": tasks._extract_claims_async,
        "summary": tasks._generate_summary_async,
        "scan": tasks._scan_rules_async,
    }
    return {phase: await runners[phase](f"task-{phase}", run_id) for phase in order}


async def test_a_run_completes_only_after_both_parallel_branches_succeed(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """扫描先、摘要后：最后完成的那一方写 completed。"""
    results = await run_all_three(pipeline_setup.id, order=("extract", "scan", "summary"))

    assert results["scan"]["run_completed"] is False, "扫描完成时摘要还没跑"
    assert results["summary"]["run_completed"] is True, "摘要收尾时应当落 completed"

    assert await phase_states(async_db_session, pipeline_setup.id) == {
        "extract": "succeeded",
        "summary": "succeeded",
        "scan": "succeeded",
    }
    run = await tasks.load_run(async_db_session, pipeline_setup.id)
    assert run.status == "completed"
    assert run.finished_at is not None


async def test_a_run_completes_in_the_other_branch_order_too(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """摘要先、扫描后：换一个完成顺序，结论必须一样。

    这是「两支线任意完成顺序」的另一半。用同一条 UPDATE 里比较另一列的写法，而不是
    「先读对方状态再写自己」—— 后者在两支线同时收尾时会双双读到对方未完成，run 永远
    停在 scanning。
    """
    results = await run_all_three(pipeline_setup.id, order=("extract", "summary", "scan"))

    assert results["summary"]["run_completed"] is False
    assert results["scan"]["run_completed"] is True

    async_db_session.expunge_all()
    run = await tasks.load_run(async_db_session, pipeline_setup.id)
    assert run.status == "completed"
    assert run.finished_at is not None


async def test_summary_alone_does_not_complete_the_run(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """摘要成功但扫描还没跑：同样不能 completed —— 告警才是作者直接消费的产物。"""
    await tasks._generate_summary_async("task-2", pipeline_setup.id)

    async_db_session.expunge_all()
    run = await tasks.load_run(async_db_session, pipeline_setup.id)
    assert run.summary_state == "succeeded"
    assert run.scan_state == "pending"
    assert run.status == "summarizing"
    assert run.finished_at is None


async def test_a_summary_failure_after_a_successful_scan_fails_the_run(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """扫描成功之后摘要失败：run 最终必须是 failed，不是 completed。"""
    await tasks._scan_rules_async("task-3", pipeline_setup.id)
    fake_provider.summary_error = RuntimeError("summary gateway down")

    with pytest.raises(RuntimeError):
        await tasks._generate_summary_async("task-2", pipeline_setup.id)

    async_db_session.expunge_all()
    run = await tasks.load_run(async_db_session, pipeline_setup.id)
    assert run.status == "failed", "任一支线失败最终就是 failed"
    assert run.error_code == "summary_error"
    assert run.scan_state == "succeeded", "已成功的支线状态保留下来，便于定位"
    assert run.summary_state == "failed"


async def test_a_scan_failure_after_a_successful_summary_fails_the_run(
    use_test_session, async_db_session, pipeline_setup, fake_provider, monkeypatch
):
    """摘要成功之后扫描失败：另一个方向同样收敛到 failed。"""
    await tasks._generate_summary_async("task-2", pipeline_setup.id)

    class ExplodingScanner:
        def __init__(self, rule_version):
            pass

        async def scan_chapter(self, **kwargs):
            raise RuntimeError("scanner bug")

    monkeypatch.setattr(tasks, "RuleScanner", ExplodingScanner)

    with pytest.raises(RuntimeError):
        await tasks._scan_rules_async("task-3", pipeline_setup.id)

    async_db_session.expunge_all()
    run = await tasks.load_run(async_db_session, pipeline_setup.id)
    assert run.status == "failed"
    assert run.error_code == "scan_error"
    assert run.summary_state == "succeeded"
    assert run.scan_state == "failed"


async def test_a_double_failure_keeps_the_first_error_as_the_root_cause(
    use_test_session, async_db_session, pipeline_setup, fake_provider, monkeypatch
):
    """两条支线都失败时，error_code/error_detail 必须停在第一个。

    回归：无条件覆盖会把根因换成后发生的次要错误 —— 而后发生的那个往往只是「因为
    前一条已经失败」的连带结果，排查时完全没用。这里让扫描先失败，再让摘要绕过
    ensure_run_not_failed 直接失败（模拟并行支线几乎同时出错）。
    """
    class ExplodingScanner:
        def __init__(self, rule_version):
            pass

        async def scan_chapter(self, **kwargs):
            raise RuntimeError("scanner bug")

    monkeypatch.setattr(tasks, "RuleScanner", ExplodingScanner)
    with pytest.raises(RuntimeError):
        await tasks._scan_rules_async("task-3", pipeline_setup.id)

    async_db_session.expunge_all()
    first = await tasks.load_run(async_db_session, pipeline_setup.id)
    first_finished_at = first.finished_at

    # 并行的摘要支线随后也失败（真实并发下它读到 failed 之前就已经在跑了）
    await tasks.fail_run(
        async_db_session,
        pipeline_setup.id,
        phase="summary",
        error_code="summary_error",
        error_detail="gateway down too",
    )

    async_db_session.expunge_all()
    run = await tasks.load_run(async_db_session, pipeline_setup.id)
    assert run.error_code == "scan_error", "根因被后发生的错误覆盖了"
    assert "scanner bug" in run.error_detail
    assert run.status == "failed"
    assert run.scan_state == "failed"
    assert run.summary_state == "failed", "两条支线的失败都要看得见"
    assert run.finished_at == first_finished_at, "终态时刻取第一个"


async def test_a_failure_does_not_overwrite_a_succeeded_phase(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """已成功支线的阶段状态不被别人的失败改写 —— 它的产出确实已经落库了。"""
    await tasks._scan_rules_async("task-3", pipeline_setup.id)

    await tasks.fail_run(
        async_db_session,
        pipeline_setup.id,
        phase="scan",
        error_code="scan_error",
        error_detail="迟到的失败",
    )

    async_db_session.expunge_all()
    run = await tasks.load_run(async_db_session, pipeline_setup.id)
    assert run.scan_state == "succeeded", "扫描的产出已落库，状态不能被翻成 failed"
    assert run.status == "scanning", "也不该把 run 打成 failed"
    assert run.error_code is None


async def test_replaying_a_finished_pipeline_is_idempotent(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """Celery 是至少一次投递：整条管道重放一遍，run 的终态与时刻都不变。"""
    fake_provider.claims = [claim_payload("李长风", "fp_1")]
    await run_all_three(pipeline_setup.id, order=("extract", "scan", "summary"))

    async_db_session.expunge_all()
    first = await tasks.load_run(async_db_session, pipeline_setup.id)
    finished_at, status = first.finished_at, first.status

    replay = await run_all_three(pipeline_setup.id, order=("extract", "summary", "scan"))

    assert all(r["status"] == "success" for r in replay.values())
    assert await phase_states(async_db_session, pipeline_setup.id) == {
        "extract": "succeeded",
        "summary": "succeeded",
        "scan": "succeeded",
    }
    run = await tasks.load_run(async_db_session, pipeline_setup.id)
    assert run.status == status == "completed"
    assert run.finished_at == finished_at, "重放刷新了完成时刻"
    assert len(await load_claims(async_db_session)) == 1, "重放不得复制 claim"


async def test_replaying_one_branch_does_not_undo_completion(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """单支线重放不得把 completed 打回 scanning。

    start_phase 会把阶段标成 running；如果它也作用于已经 succeeded 的阶段，
    ck_consistency_run_completed_phases 会直接拒绝这条 UPDATE（completed 蕴含三阶段
    成功），重放的任务连启动都做不到。
    """
    await run_all_three(pipeline_setup.id, order=("extract", "summary", "scan"))

    await tasks._scan_rules_async("task-3-retry", pipeline_setup.id)

    async_db_session.expunge_all()
    run = await tasks.load_run(async_db_session, pipeline_setup.id)
    assert run.status == "completed"
    assert run.scan_state == "succeeded"


async def test_completed_status_cannot_exist_without_all_phases_succeeding(
    use_test_session, async_db_session, pipeline_setup
):
    """数据库层面的兜底：手写 completed 而阶段没齐，CHECK 必须拒绝。

    应用逻辑之外还需要这道约束：任何绕过 succeed_phase 的写入路径（数据修复脚本、
    未来的新任务）都不能造出「显示完成、实际没跑完」的 run。
    """
    from sqlalchemy import update as sa_update
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await async_db_session.execute(
            sa_update(tasks.ConsistencyRun)
            .where(tasks.ConsistencyRun.id == pipeline_setup.id)
            .values(status="completed")
        )
        await async_db_session.commit()
    await async_db_session.rollback()


async def test_status_does_not_regress_when_branches_run_out_of_order(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """摘要与扫描并行：慢的一方不得把粗粒度 status 打回 summarizing。"""
    await tasks._scan_rules_async("task-3", pipeline_setup.id)
    await tasks._generate_summary_async("task-2", pipeline_setup.id)

    async_db_session.expunge_all()
    run = await tasks.load_run(async_db_session, pipeline_setup.id)
    assert run.status not in ("summarizing", "pending"), "进度倒退了"
    # extract 支线没跑过，所以还不能 completed —— 但也绝不能倒退
    assert run.status == "scanning"
    assert run.summary_state == "succeeded"
    assert run.scan_state == "succeeded"
    assert run.extract_state == "pending"


async def test_advance_run_status_is_conditional(
    use_test_session, async_db_session, pipeline_setup
):
    changed = await tasks.advance_run_status(
        async_db_session, pipeline_setup.id, "extracting", allowed_from=("pending",)
    )
    assert changed is True

    unchanged = await tasks.advance_run_status(
        async_db_session, pipeline_setup.id, "summarizing", allowed_from=("pending",)
    )
    assert unchanged is False, "不在 allowed_from 内的状态不得被改写"

    async_db_session.expunge_all()
    run = await tasks.load_run(async_db_session, pipeline_setup.id)
    assert run.status == "extracting"


# --- 唯一键并发幂等 -----------------------------------------------------------


async def test_get_or_create_run_returns_the_same_run(
    use_test_session, async_db_session, seed_project
):
    """同 (chapter, body_rev, pipeline_version) 始终是同一个 run。"""
    await seed_project(chapter_ids=("ch_a",))

    first = await get_or_create_run(
        async_db_session,
        project_id="proj_a",
        chapter_id="ch_a",
        body_rev=3,
        pipeline_version=PIPELINE_VERSION,
        trigger="body_save",
    )
    await async_db_session.commit()

    second = await get_or_create_run(
        async_db_session,
        project_id="proj_a",
        chapter_id="ch_a",
        body_rev=3,
        pipeline_version=PIPELINE_VERSION,
        trigger="manual_scan",
    )
    await async_db_session.commit()

    assert first == second

    from db.models_consistency_extended import ConsistencyRun

    async_db_session.expunge_all()
    runs = list(
        (await async_db_session.execute(select(ConsistencyRun))).scalars().all()
    )
    assert len(runs) == 1, "不得为同一版本建两个 run"


async def test_get_or_create_run_survives_a_concurrent_insert(
    use_test_session, async_db_session, seed_project, monkeypatch
):
    """并发插入撞 uq_consistency_run_key 时读回已存在的 run，而不是抛错。

    模拟真实竞态：本 worker 查完发现没有，另一个 worker 在它插入之前先插进去了。
    这里在「查完之后、插入之前」注入那一行。
    """
    from db.models_consistency_extended import ConsistencyRun
    from services import consistency as consistency_service

    await seed_project(chapter_ids=("ch_a",))

    original_select = consistency_service._select_run_id
    calls = {"n": 0}

    async def racing_select(db, **kwargs):
        result = await original_select(db, **kwargs)
        calls["n"] += 1
        if calls["n"] == 1 and result is None:
            # 另一个 worker 抢先插入了同一个 run
            db.add(
                ConsistencyRun(
                    project_id="proj_a",
                    chapter_id="ch_a",
                    body_rev=4,
                    pipeline_version=PIPELINE_VERSION,
                    status="pending",
                    trigger="body_save",
                )
            )
            await db.flush()
        return result

    monkeypatch.setattr(consistency_service, "_select_run_id", racing_select)

    run_id = await get_or_create_run(
        async_db_session,
        project_id="proj_a",
        chapter_id="ch_a",
        body_rev=4,
        pipeline_version=PIPELINE_VERSION,
        trigger="body_save",
    )
    await async_db_session.commit()

    async_db_session.expunge_all()
    runs = list(
        (
            await async_db_session.execute(
                select(ConsistencyRun).where(ConsistencyRun.body_rev == 4)
            )
        )
        .scalars()
        .all()
    )
    assert len(runs) == 1, "竞态不得产生两个 run"
    assert runs[0].id == run_id, "返回的是实际存在的那个 run"


async def test_upsert_active_summary_updates_in_place(
    use_test_session, async_db_session, seed_project
):
    """同 (owner, source_rev, summary_version) 只有一行。"""
    await seed_project(chapter_ids=("ch_a",))

    await upsert_active_summary(
        async_db_session,
        owner_type="chapter",
        owner_id="ch_a",
        source_rev=1,
        summary_version="1.0.0",
        content="第一次写",
        model_id="model-a",
        token_count=10,
    )
    await async_db_session.commit()

    await upsert_active_summary(
        async_db_session,
        owner_type="chapter",
        owner_id="ch_a",
        source_rev=1,
        summary_version="1.0.0",
        content="第二次写",
        model_id="model-b",
        token_count=20,
    )
    await async_db_session.commit()

    async_db_session.expunge_all()
    summaries = list(
        (await async_db_session.execute(select(DocumentSummary))).scalars().all()
    )
    assert len(summaries) == 1, "只有一行，不是两行"
    assert summaries[0].content == "第二次写"
    assert summaries[0].model_id == "model-b"


async def test_upsert_active_summary_survives_a_concurrent_insert(
    use_test_session, async_db_session, seed_project, monkeypatch
):
    """并发插入撞 uq_document_summary_key 时读回已存在行并继续更新，而不是抛错。

    模拟真实竞态：本 worker 查完发现没有，另一个 worker 在它插入之前先提交了同一
    个键。在「查完之后、插入之前」注入并提交那一行，本 worker 的插入就会真的撞键。
    """
    from services import consistency as consistency_service

    await seed_project(chapter_ids=("ch_a",))
    await async_db_session.commit()

    original_select = consistency_service._select_summary_row
    calls = {"n": 0}

    async def racing_select(db, **kwargs):
        row = await original_select(db, **kwargs)
        calls["n"] += 1
        if calls["n"] == 1 and row is None:
            # 另一个 worker 抢先写入并提交了同一 (owner, source_rev, version)
            db.add(
                DocumentSummary(
                    owner_type="chapter",
                    owner_id="ch_a",
                    source_rev=1,
                    summary_version="1.0.0",
                    content="抢先写入",
                    model_id="model-race",
                    token_count=1,
                    status="active",
                )
            )
            await db.commit()
        return row

    monkeypatch.setattr(consistency_service, "_select_summary_row", racing_select)

    summary = await upsert_active_summary(
        async_db_session,
        owner_type="chapter",
        owner_id="ch_a",
        source_rev=1,
        summary_version="1.0.0",
        content="本 worker 的摘要",
        model_id="model-mine",
        token_count=5,
    )
    await async_db_session.commit()

    assert calls["n"] == 2, "撞键后必须重新读一次"
    async_db_session.expunge_all()
    summaries = list(
        (await async_db_session.execute(select(DocumentSummary))).scalars().all()
    )
    assert len(summaries) == 1, "竞态不得产生两行"
    assert summaries[0].id == summary.id, "返回的是实际存在的那一行"
    assert summaries[0].content == "本 worker 的摘要", "读回已存在行后继续更新"
    assert summaries[0].model_id == "model-mine"


# --- 管道调度 -----------------------------------------------------------------


async def test_dispatch_reuses_existing_run_for_same_revision(
    use_test_session, async_db_session, pipeline_setup, monkeypatch
):
    """同 (chapter, body_rev, pipeline_version) 不重复建 run。"""
    dispatched = []

    class FakeChain:
        def __init__(self, *args):
            dispatched.append(args)

        def apply_async(self):
            return None

    monkeypatch.setattr(tasks, "chain", FakeChain)

    result = await tasks._process_body_saved_async(
        "task-0",
        {
            "project_id": "proj_a",
            "chapter_id": "ch_a",
            "body_rev": 1,
            "trigger": "user_edit",
        },
    )

    assert result["run_id"] == pipeline_setup.id
    assert result["status"] == "dispatched"
    assert len(dispatched) == 1


async def test_dispatch_creates_run_with_shared_pipeline_version(
    use_test_session, async_db_session, seed_project, monkeypatch
):
    await seed_project(chapter_ids=("ch_a",))

    class FakeChain:
        def __init__(self, *args):
            pass

        def apply_async(self):
            return None

    monkeypatch.setattr(tasks, "chain", FakeChain)

    result = await tasks._process_body_saved_async(
        "task-0",
        {
            "project_id": "proj_a",
            "chapter_id": "ch_a",
            "body_rev": 3,
            "trigger": "manual_scan",
        },
    )

    run = await tasks.load_run(async_db_session, result["run_id"])
    assert run.pipeline_version == PIPELINE_VERSION
    assert run.trigger == "manual_scan"
    assert run.body_rev == 3


async def test_summary_and_scan_are_dispatched_in_parallel(
    use_test_session, async_db_session, pipeline_setup, monkeypatch
):
    """回归：扫描只依赖已落库的 claim，串在摘要后面会白等几十秒模型调用。"""
    calls = []

    class RecordingChain:
        def __init__(self, *args):
            calls.append(("chain", args))

        def apply_async(self):
            return None

    class RecordingGroup:
        def __init__(self, *args):
            calls.append(("group", args))

        def apply_async(self):
            return None

    monkeypatch.setattr(tasks, "chain", RecordingChain)
    monkeypatch.setattr(tasks, "group", RecordingGroup)

    await tasks._process_body_saved_async(
        "task-0",
        {
            "project_id": "proj_a",
            "chapter_id": "ch_a",
            "body_rev": 1,
            "trigger": "body_save",
        },
    )

    kinds = [kind for kind, _ in calls]
    assert kinds == ["group", "chain"], "先构造并行组，再串到 extract 之后"

    group_args = next(args for kind, args in calls if kind == "group")
    assert len(group_args) == 2, "摘要与扫描在同一个并行组内"

    chain_args = next(args for kind, args in calls if kind == "chain")
    assert len(chain_args) == 2, "chain 只有两段：extract，然后并行组"
