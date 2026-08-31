"""
一致性管道任务的行为测试。

这些测试直接调用任务内部的 async 实现，并把 AsyncSessionLocal 换成测试会话，
从而在不起 Celery worker、不连 PostgreSQL 的前提下验证真实数据库行为。

重点覆盖：
* 读的是不可变快照 ChapterVersion，作者中途再存也不会串版本；
* 失败抛异常（Celery chain 才会中断），而不是返回 {"status": "error"}；
* 新版本发布时旧 claim 原子作废；
* 摘要有效版本的原子切换与并发下的头版本校验。
"""
import contextlib

import httpx
import pytest
from sqlalchemy import select

from config import settings
from db.models_consistency_extended import ConsistencyClaim, DocumentSummary
from db.models_core import ChapterVersion
from db.models_guard import GuardIssue
from services.consistency import PIPELINE_VERSION, normalize_run_trigger
from tasks import consistency as tasks


@pytest.fixture
def use_test_session(async_db_session, monkeypatch):
    """把任务里的 AsyncSessionLocal 换成测试会话（不真正开关连接）。"""

    @contextlib.asynccontextmanager
    async def _session_factory():
        yield async_db_session

    monkeypatch.setattr(tasks, "AsyncSessionLocal", _session_factory)
    return async_db_session


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
    """project + chapter + 一版不可变快照 + 一个 run。"""
    await seed_project(chapter_ids=("ch_a",))
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
    await async_db_session.flush()
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


async def add_version(db, *, rev: int, html: str):
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
    await db.flush()


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
    use_test_session, pipeline_setup, fake_provider, make_run
):
    """回归：读可变的 ChapterBody 会拿到最新正文，却按旧 body_rev 记账。"""
    await add_version(use_test_session, rev=2, html="<p>第二版正文</p>")
    run_for_rev_1 = pipeline_setup

    # rev 1 的 run 已经过时，必须失败而不是拿第二版正文冒充第一版
    with pytest.raises(tasks.StaleRevisionError):
        await tasks._extract_claims_async("task-1", run_for_rev_1.id)

    assert fake_provider.seen_html == []


async def test_missing_snapshot_fails_the_run(
    use_test_session, async_db_session, seed_project, make_run, fake_provider
):
    await seed_project(chapter_ids=("ch_a",))
    run = make_run(project_id="proj_a", chapter_id="ch_a", body_rev=5)
    async_db_session.add(run)
    await async_db_session.flush()

    with pytest.raises(tasks.BodyRevisionMissingError):
        await tasks._extract_claims_async("task-1", run.id)

    async_db_session.expunge_all()
    refreshed = await tasks.load_run(async_db_session, run.id)
    assert refreshed.status == "failed"
    assert refreshed.error_code == "body_not_found"


async def test_unknown_run_id_raises(use_test_session, fake_provider):
    """run 不存在时抛错，让 chain 中断（原实现返回 error dict，chain 继续跑）。"""
    with pytest.raises(tasks.RunNotFoundError):
        await tasks._extract_claims_async("task-1", 999999)


# --- 失败必须抛异常 -----------------------------------------------------------


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

    result = await async_db_session.execute(select(ConsistencyClaim))
    assert list(result.scalars().all()) == []


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


# --- claim 落库与作废 ---------------------------------------------------------


async def test_claims_are_persisted_with_run_revision(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    fake_provider.claims = [
        claim_payload("李长风", "fp_1"),
        claim_payload("王二", "fp_2", object_value="false"),
    ]

    result = await tasks._extract_claims_async("task-1", pipeline_setup.id)

    assert result["claims_count"] == 2
    claims = list(
        (await async_db_session.execute(select(ConsistencyClaim))).scalars().all()
    )
    assert len(claims) == 2
    assert {claim.subject_text for claim in claims} == {"李长风", "王二"}
    assert all(claim.chapter_id == "ch_a" for claim in claims)
    assert all(claim.body_rev == 1 for claim in claims)
    assert all(claim.source_kind == "body" for claim in claims)
    assert all(claim.status == "accepted" for claim in claims)


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
    async_db_session.expunge_all()
    claims = list(
        (
            await async_db_session.execute(
                select(ConsistencyClaim).order_by(ConsistencyClaim.body_rev)
            )
        )
        .scalars()
        .all()
    )
    assert [(claim.body_rev, claim.status) for claim in claims] == [
        (1, "superseded"),
        (2, "accepted"),
    ]


async def test_rerunning_same_revision_is_idempotent(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    """同一版本重跑不会留下两组 accepted claim。"""
    fake_provider.claims = [claim_payload("李长风", "fp_1")]
    await tasks._extract_claims_async("task-1", pipeline_setup.id)
    await tasks._extract_claims_async("task-1-retry", pipeline_setup.id)

    async_db_session.expunge_all()
    accepted = list(
        (
            await async_db_session.execute(
                select(ConsistencyClaim).where(ConsistencyClaim.status == "accepted")
            )
        )
        .scalars()
        .all()
    )
    assert len(accepted) == 1


async def test_superseding_is_scoped_to_the_chapter(
    use_test_session, async_db_session, seed_project, make_claim, pipeline_setup, fake_provider, make_run
):
    """作废只针对本章，别的章节的 claim 不受影响。"""
    from db.models_core import Chapter

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
    other = make_claim(
        project_id="proj_a",
        chapter_id="ch_b",
        body_rev=1,
        subject_text="别章角色",
        fingerprint="fp_other",
        status="accepted",
    )
    async_db_session.add(other)
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

    claims = list(
        (await async_db_session.execute(select(ConsistencyClaim))).scalars().all()
    )
    assert claims == []
    async_db_session.expunge_all()
    run = await tasks.load_run(async_db_session, run_id)
    assert run.status == "failed"
    assert run.error_code == "stale_revision"


# --- 摘要 ---------------------------------------------------------------------


async def test_summary_is_persisted_with_configured_model(
    use_test_session, async_db_session, pipeline_setup, fake_provider, monkeypatch
):
    """model_id 来自配置，不是硬编码的 gpt-4o-mini。"""
    monkeypatch.setattr(settings, "consistency_summary_model", "custom-summary-model")
    fake_provider.summary = ("这一章的摘要", 88)

    await tasks._generate_summary_async("task-2", pipeline_setup.id)

    summary = (
        await async_db_session.execute(select(DocumentSummary))
    ).scalar_one()
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


async def test_scan_completes_the_run(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    result = await tasks._scan_rules_async("task-3", pipeline_setup.id)

    assert result["status"] == "success"
    async_db_session.expunge_all()
    run = await tasks.load_run(async_db_session, pipeline_setup.id)
    assert run.status == "completed"
    assert run.finished_at is not None


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


async def test_scan_aborts_when_revision_advanced(
    use_test_session, async_db_session, pipeline_setup, fake_provider
):
    await add_version(async_db_session, rev=2, html="<p>新版</p>")

    with pytest.raises(tasks.StaleRevisionError):
        await tasks._scan_rules_async("task-3", pipeline_setup.id)

    async_db_session.expunge_all()
    run = await tasks.load_run(async_db_session, pipeline_setup.id)
    assert run.status == "failed"
    assert run.error_code == "stale_revision"


# --- 管道调度 -----------------------------------------------------------------


async def test_dispatch_reuses_existing_run_for_same_revision(
    use_test_session, async_db_session, pipeline_setup, monkeypatch
):
    """同 (chapter, body_rev, pipeline_version) 不重复建 run。"""
    dispatched = []

    class FakeChain:
        def __init__(self, *signatures):
            dispatched.append(signatures)

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
    assert len(dispatched[0]) == 3


async def test_dispatch_creates_run_with_shared_pipeline_version(
    use_test_session, async_db_session, seed_project, monkeypatch
):
    await seed_project(chapter_ids=("ch_a",))

    class FakeChain:
        def __init__(self, *signatures):
            pass

        def apply_async(self):
            return None

    monkeypatch.setattr(tasks, "chain", FakeChain)

    result = await tasks._process_body_saved_async(
        "task-0",
        {"project_id": "proj_a", "chapter_id": "ch_a", "body_rev": 3, "trigger": "manual_scan"},
    )

    run = await tasks.load_run(async_db_session, result["run_id"])
    assert run.pipeline_version == PIPELINE_VERSION
    assert run.trigger == "manual_scan"
    assert run.body_rev == 3
