"""
RuleScanner 的行为测试。

覆盖三条 P0 规则（生死冲突 / 归属冲突 / 知情边界）、跨章检测、issue 生命周期
（去重、复现、stale、误报不复活）、锚点与证据的正确性。

所有断言都基于真实 ORM 字段：GuardIssue 有 issue_type / description / evidence /
anchor / actions（没有 rule_id / title / message）；ConsistencyRun 位于
db.models_consistency_extended；scan_chapter 返回 list[str]（issue id）。
"""
import pytest
from sqlalchemy import select

from db.models_consistency_extended import GuardIssueEvidence
from db.models_guard import GuardIssue
from services.rule_scanner import ISSUE_ACTIONS, RuleScanner

RESOLUTION_ACTIONS = {
    "accept_old_fact",
    "accept_new_fact",
    "intentional_exception",
    "false_positive",
    "fixed_in_body",
    "defer",
}


@pytest.fixture
def scanner():
    return RuleScanner(rule_version="1.0.0")


@pytest.fixture
async def scan_context(async_db_session, seed_project, make_run):
    """建好 project + 两章 + 一个 run，返回 (run_id, scan 调用器)。"""
    await seed_project(chapter_ids=("ch_a", "ch_b"))
    run = make_run(project_id="proj_a", chapter_id="ch_a")
    async_db_session.add(run)
    await async_db_session.flush()
    return run


@pytest.fixture
def add_claim(async_db_session, make_claim):
    """插入一条 accepted claim 并返回它（已 flush，拿到自增 id）。"""

    async def _add(**overrides):
        overrides.setdefault("project_id", "proj_a")
        overrides.setdefault("chapter_id", "ch_a")
        claim = make_claim(**overrides)
        async_db_session.add(claim)
        await async_db_session.flush()
        return claim

    return _add


@pytest.fixture
def add_entry(async_db_session):
    """插入 codex 条目。

    subject_entry_id / object_entry_id 是指向 codex_entries 的真实外键，
    只写 id 字符串会触发 FOREIGN KEY constraint failed。
    """
    from db.models_codex import CodexEntry

    async def _add(entry_id: str, *, kind: str = "character", project_id: str = "proj_a"):
        entry = CodexEntry(
            id=entry_id,
            project_id=project_id,
            kind=kind,
            name=f"条目{entry_id}",
            description="描述",
            attrs={},
            resident=False,
            status="confirmed",
            ref_chapters=[],
            conflicts=[],
        )
        async_db_session.add(entry)
        await async_db_session.flush()
        return entry

    return _add


async def run_scan(scanner, db, run, *, chapter_id="ch_a", body_rev=1):
    return await scanner.scan_chapter(
        db=db,
        run_id=run.id,
        project_id="proj_a",
        chapter_id=chapter_id,
        body_rev=body_rev,
    )


async def load_issues(db) -> list[GuardIssue]:
    result = await db.execute(select(GuardIssue).order_by(GuardIssue.id))
    return list(result.scalars().all())


# --- 规则 1：生死冲突 ----------------------------------------------------------


async def test_death_then_alive_is_reported(
    scanner, async_db_session, scan_context, add_claim
):
    """先死后活且无解释 → alive_conflict。"""
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        timeline_id="main",
        story_order=10.0,
        paragraph_id="p10",
    )
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="true",
        timeline_id="main",
        story_order=20.0,
        paragraph_id="p20",
        fingerprint="fp_alive_true",
    )

    issue_ids = await run_scan(scanner, async_db_session, scan_context)

    assert len(issue_ids) == 1
    issues = await load_issues(async_db_session)
    assert issues[0].issue_type == "alive_conflict"
    assert issues[0].severity == "high"
    assert issues[0].status == "open"
    assert "李长风" in issues[0].description


async def test_alive_then_death_is_not_reported(
    scanner, async_db_session, scan_context, add_claim
):
    """先活后死是正常叙事，不该报警。"""
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="true",
        timeline_id="main",
        story_order=10.0,
    )
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        timeline_id="main",
        story_order=20.0,
        fingerprint="fp_alive_false",
    )

    assert await run_scan(scanner, async_db_session, scan_context) == []


async def test_claims_without_story_order_are_skipped(
    scanner, async_db_session, scan_context, add_claim
):
    """时间顺序未知时无法判断先后，宁可漏报也不误报。"""
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        timeline_id="main",
        story_order=None,
    )
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="true",
        timeline_id="main",
        story_order=None,
        fingerprint="fp_alive_true",
    )

    assert await run_scan(scanner, async_db_session, scan_context) == []


async def test_conflicts_are_not_reported_across_timelines(
    scanner, async_db_session, scan_context, add_claim
):
    """不同时间线（回忆/平行线）之间不比对。"""
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        timeline_id="main",
        story_order=10.0,
    )
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="true",
        timeline_id="flashback",
        story_order=20.0,
        fingerprint="fp_alive_true",
    )

    assert await run_scan(scanner, async_db_session, scan_context) == []


async def test_conflicts_are_not_reported_across_subjects(
    scanner, async_db_session, scan_context, add_claim
):
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        timeline_id="main",
        story_order=10.0,
    )
    await add_claim(
        subject_text="王二",
        predicate="alive",
        object_value="true",
        timeline_id="main",
        story_order=20.0,
        fingerprint="fp_wang_alive",
    )

    assert await run_scan(scanner, async_db_session, scan_context) == []


async def test_subject_identity_uses_entry_id_when_resolved(
    scanner, async_db_session, scan_context, add_claim, add_entry
):
    """同一实体用了不同称呼（但 subject_entry_id 相同）时仍能发现冲突。"""
    await add_entry("cx_lee")
    await add_claim(
        subject_text="李长风",
        subject_entry_id="cx_lee",
        predicate="alive",
        object_value="false",
        timeline_id="main",
        story_order=10.0,
    )
    await add_claim(
        subject_text="长风",
        subject_entry_id="cx_lee",
        predicate="alive",
        object_value="true",
        timeline_id="main",
        story_order=20.0,
        fingerprint="fp_changfeng_alive",
    )

    issue_ids = await run_scan(scanner, async_db_session, scan_context)

    assert len(issue_ids) == 1


async def test_conflict_across_chapters_is_detected(
    scanner, async_db_session, scan_context, add_claim
):
    """跨章冲突：死在 ch_a，活在 ch_b。"""
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        chapter_id="ch_a",
        timeline_id="main",
        story_order=10.0,
    )
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="true",
        chapter_id="ch_b",
        body_rev=3,
        timeline_id="main",
        story_order=20.0,
        paragraph_id="p_b7",
        fingerprint="fp_alive_true_b",
    )

    issue_ids = await run_scan(scanner, async_db_session, scan_context)

    assert len(issue_ids) == 1
    issues = await load_issues(async_db_session)
    # issue 归属到出问题的那一章，而非被扫描的那一章
    assert issues[0].chapter_id == "ch_b"


# --- 锚点 ---------------------------------------------------------------------


async def test_anchor_identifies_offending_chapter_and_body_rev(
    scanner, async_db_session, scan_context, add_claim
):
    """回归：锚点只有 pid 无法定位到哪一版正文，必须带 chapter_id + body_rev。"""
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        chapter_id="ch_a",
        body_rev=1,
        timeline_id="main",
        story_order=10.0,
    )
    offender = await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="true",
        chapter_id="ch_b",
        body_rev=7,
        paragraph_id="p_b42",
        timeline_id="main",
        story_order=20.0,
        fingerprint="fp_alive_true_b",
    )

    await run_scan(scanner, async_db_session, scan_context)

    issues = await load_issues(async_db_session)
    anchor = issues[0].anchor
    assert anchor["chapter_id"] == "ch_b"
    assert anchor["body_rev"] == 7
    assert anchor["pid"] == "p_b42"
    assert anchor["claim_id"] == offender.id


async def test_evidence_payload_records_claim_ids_and_rule_version(
    scanner, async_db_session, scan_context, add_claim
):
    first = await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        timeline_id="main",
        story_order=10.0,
    )
    second = await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="true",
        timeline_id="main",
        story_order=20.0,
        fingerprint="fp_alive_true",
    )

    await run_scan(scanner, async_db_session, scan_context)

    issues = await load_issues(async_db_session)
    assert issues[0].evidence["claim_ids"] == [first.id, second.id]
    assert issues[0].evidence["rule_version"] == "1.0.0"
    assert issues[0].rule_version == "1.0.0"


async def test_evidence_rows_are_written_with_expected_and_actual_sides(
    scanner, async_db_session, scan_context, add_claim
):
    first = await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        timeline_id="main",
        story_order=10.0,
    )
    second = await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="true",
        chapter_id="ch_b",
        body_rev=4,
        timeline_id="main",
        story_order=20.0,
        fingerprint="fp_alive_true",
    )

    issue_ids = await run_scan(scanner, async_db_session, scan_context)

    result = await async_db_session.execute(
        select(GuardIssueEvidence)
        .where(GuardIssueEvidence.issue_id == issue_ids[0])
        .order_by(GuardIssueEvidence.sort_order)
    )
    rows = list(result.scalars().all())
    assert [row.side for row in rows] == ["expected", "actual"]
    assert [row.claim_id for row in rows] == [first.id, second.id]
    assert rows[1].chapter_id == "ch_b"
    assert rows[1].body_rev == 4
    assert all(row.source_kind == "claim" for row in rows)


async def test_rescanning_does_not_duplicate_evidence_rows(
    scanner, async_db_session, scan_context, add_claim
):
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        timeline_id="main",
        story_order=10.0,
    )
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="true",
        timeline_id="main",
        story_order=20.0,
        fingerprint="fp_alive_true",
    )

    issue_ids = await run_scan(scanner, async_db_session, scan_context)
    await run_scan(scanner, async_db_session, scan_context)

    result = await async_db_session.execute(
        select(GuardIssueEvidence).where(GuardIssueEvidence.issue_id == issue_ids[0])
    )
    assert len(list(result.scalars().all())) == 2


# --- actions 与 resolve API 的契约 ---------------------------------------------


async def test_offered_actions_are_accepted_by_the_resolve_api(
    scanner, async_db_session, scan_context, add_claim
):
    """回归：actions 曾是 keep-old/keep-new/intentional，提交处置会被 422 拒。"""
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        timeline_id="main",
        story_order=10.0,
    )
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="true",
        timeline_id="main",
        story_order=20.0,
        fingerprint="fp_alive_true",
    )

    await run_scan(scanner, async_db_session, scan_context)

    issues = await load_issues(async_db_session)
    assert issues[0].actions
    assert set(issues[0].actions) <= RESOLUTION_ACTIONS


def test_every_issue_type_offers_only_valid_actions():
    for issue_type, actions in ISSUE_ACTIONS.items():
        assert actions, issue_type
        assert set(actions) <= RESOLUTION_ACTIONS, issue_type


# --- issue 生命周期 -----------------------------------------------------------


async def test_same_conflict_is_deduplicated_across_scans(
    scanner, async_db_session, scan_context, add_claim
):
    """同一冲突重复扫描不产生新 issue。"""
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        timeline_id="main",
        story_order=10.0,
    )
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="true",
        timeline_id="main",
        story_order=20.0,
        fingerprint="fp_alive_true",
    )

    first_ids = await run_scan(scanner, async_db_session, scan_context)
    second_ids = await run_scan(scanner, async_db_session, scan_context)

    assert first_ids == second_ids
    assert len(await load_issues(async_db_session)) == 1


async def test_fingerprint_survives_reextraction_with_new_claim_rows(
    scanner, async_db_session, scan_context, add_claim
):
    """关键回归：指纹曾包含 claim 行 id，重新抽取后同一冲突会变成新 issue。"""
    old_dead = await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        timeline_id="main",
        story_order=10.0,
    )
    old_alive = await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="true",
        timeline_id="main",
        story_order=20.0,
        fingerprint="fp_alive_true",
    )
    first_ids = await run_scan(scanner, async_db_session, scan_context)

    # 模拟正文重存后重新抽取：旧 claim 作废，插入内容相同但 id 不同的新行
    old_dead.status = "superseded"
    old_alive.status = "superseded"
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        body_rev=2,
        timeline_id="main",
        story_order=10.0,
        fingerprint="fp_dead_rev2",
    )
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="true",
        body_rev=2,
        timeline_id="main",
        story_order=20.0,
        fingerprint="fp_alive_rev2",
    )

    second_ids = await run_scan(scanner, async_db_session, scan_context, body_rev=2)

    assert second_ids == first_ids, "same conflict got a new issue id after re-extraction"
    assert len(await load_issues(async_db_session)) == 1


async def test_rediscovered_issue_bumps_issue_rev(
    scanner, async_db_session, scan_context, add_claim
):
    """再次命中时 issue_rev 递增，客户端才能察觉内容变化。"""
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        timeline_id="main",
        story_order=10.0,
    )
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="true",
        timeline_id="main",
        story_order=20.0,
        fingerprint="fp_alive_true",
    )

    await run_scan(scanner, async_db_session, scan_context)
    issues = await load_issues(async_db_session)
    assert issues[0].issue_rev == 1

    await run_scan(scanner, async_db_session, scan_context)
    async_db_session.expunge_all()
    issues = await load_issues(async_db_session)
    assert issues[0].issue_rev == 2


async def test_disappearing_conflict_is_marked_stale(
    scanner, async_db_session, scan_context, add_claim
):
    """冲突被改掉后，旧 issue 标为 stale 并记录时间。"""
    dead = await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        timeline_id="main",
        story_order=10.0,
    )
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="true",
        timeline_id="main",
        story_order=20.0,
        fingerprint="fp_alive_true",
    )
    await run_scan(scanner, async_db_session, scan_context)

    # 作者删掉了「死亡」那句
    dead.status = "superseded"
    await async_db_session.flush()
    await run_scan(scanner, async_db_session, scan_context)

    async_db_session.expunge_all()
    issues = await load_issues(async_db_session)
    assert issues[0].status == "stale"
    assert issues[0].stale_at is not None


async def test_reappearing_conflict_reopens_stale_issue(
    scanner, async_db_session, scan_context, add_claim
):
    """冲突回来时复用同一条 issue 并重开，而不是新建。"""
    dead = await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        timeline_id="main",
        story_order=10.0,
    )
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="true",
        timeline_id="main",
        story_order=20.0,
        fingerprint="fp_alive_true",
    )
    first_ids = await run_scan(scanner, async_db_session, scan_context)

    dead.status = "superseded"
    await async_db_session.flush()
    await run_scan(scanner, async_db_session, scan_context)

    dead.status = "accepted"
    await async_db_session.flush()
    reopened_ids = await run_scan(scanner, async_db_session, scan_context)

    assert reopened_ids == first_ids
    async_db_session.expunge_all()
    issues = await load_issues(async_db_session)
    assert len(issues) == 1
    assert issues[0].status == "open"
    assert issues[0].stale_at is None
    assert issues[0].resolved is False


async def test_false_positive_issue_is_not_revived(
    scanner, async_db_session, scan_context, add_claim
):
    """作者判定误报后，再次命中不得把它改回 open。"""
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        timeline_id="main",
        story_order=10.0,
    )
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="true",
        timeline_id="main",
        story_order=20.0,
        fingerprint="fp_alive_true",
    )
    await run_scan(scanner, async_db_session, scan_context)

    issues = await load_issues(async_db_session)
    issues[0].status = "false_positive"
    issues[0].false_positive = True
    issues[0].resolution = "false_positive"
    await async_db_session.flush()
    revision_before = issues[0].issue_rev

    await run_scan(scanner, async_db_session, scan_context)

    async_db_session.expunge_all()
    refreshed = await load_issues(async_db_session)
    assert refreshed[0].status == "false_positive"
    assert refreshed[0].false_positive is True
    assert refreshed[0].issue_rev == revision_before


async def test_stale_marking_is_scoped_to_the_scanned_chapter(
    scanner, async_db_session, scan_context, add_claim
):
    """扫描 ch_a 不得把 ch_b 的 open issue 标成 stale。"""
    other = GuardIssue(
        id="gi_other",
        project_id="proj_a",
        chapter_id="ch_b",
        run_id=scan_context.id,
        issue_type="alive_conflict",
        rule_version="1.0.0",
        fingerprint="fp_unrelated",
        severity="high",
        confidence=0.9,
        description="别的章的问题",
        evidence={},
        anchor={"chapter_id": "ch_b", "body_rev": 1, "pid": "p1"},
        actions=["accept_old_fact"],
        status="open",
        issue_rev=1,
        resolved=False,
        false_positive=False,
    )
    async_db_session.add(other)
    await async_db_session.flush()

    await run_scan(scanner, async_db_session, scan_context, chapter_id="ch_a")

    async_db_session.expunge_all()
    result = await async_db_session.execute(
        select(GuardIssue).where(GuardIssue.id == "gi_other")
    )
    assert result.scalar_one().status == "open"


async def test_scan_ignores_non_accepted_claims(
    scanner, async_db_session, scan_context, add_claim
):
    """candidate/superseded/rejected 的 claim 不参与规则判定。"""
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        timeline_id="main",
        story_order=10.0,
        status="candidate",
    )
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="true",
        timeline_id="main",
        story_order=20.0,
        fingerprint="fp_alive_true",
    )

    assert await run_scan(scanner, async_db_session, scan_context) == []


async def test_scan_is_scoped_to_project(
    scanner, async_db_session, scan_context, add_claim, make_project, make_chapter
):
    """别的项目的 claim 不会串进本项目的扫描。"""
    async_db_session.add(make_project("proj_other", owner_id="user_a"))
    await async_db_session.flush()
    async_db_session.add(make_chapter("ch_other", project_id="proj_other", idx=1024))
    await async_db_session.flush()

    await add_claim(
        project_id="proj_other",
        chapter_id="ch_other",
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        timeline_id="main",
        story_order=10.0,
    )
    await add_claim(
        project_id="proj_other",
        chapter_id="ch_other",
        subject_text="李长风",
        predicate="alive",
        object_value="true",
        timeline_id="main",
        story_order=20.0,
        fingerprint="fp_alive_true_other",
    )

    assert await run_scan(scanner, async_db_session, scan_context) == []


async def test_empty_project_produces_no_issues(scanner, async_db_session, scan_context):
    assert await run_scan(scanner, async_db_session, scan_context) == []


# --- 规则 2：归属冲突 ---------------------------------------------------------


@pytest.fixture
async def ownership_entries(add_entry):
    """归属规则用到的实体（都是 codex_entries 的真实外键目标）。"""
    await add_entry("cx_lee")
    await add_entry("cx_wang")
    await add_entry("cx_sword", kind="item")
    await add_entry("cx_shield", kind="item")


async def test_overlapping_ownership_is_reported(
    scanner, async_db_session, scan_context, add_claim, ownership_entries
):
    """同一物品在重叠区间内属于两个不同主人 → ownership_conflict。"""
    await add_claim(
        subject_text="李长风",
        subject_entry_id="cx_lee",
        predicate="owns",
        object_type="entity",
        object_entry_id="cx_sword",
        object_value="cx_sword",
        timeline_id="main",
        story_order=10.0,
        valid_from_order=10.0,
        valid_to_order=None,
    )
    await add_claim(
        subject_text="王二",
        subject_entry_id="cx_wang",
        predicate="owns",
        object_type="entity",
        object_entry_id="cx_sword",
        object_value="cx_sword",
        timeline_id="main",
        story_order=20.0,
        valid_from_order=20.0,
        fingerprint="fp_wang_owns",
    )

    issue_ids = await run_scan(scanner, async_db_session, scan_context)

    assert len(issue_ids) == 1
    issues = await load_issues(async_db_session)
    assert issues[0].issue_type == "ownership_conflict"


async def test_sequential_ownership_transfer_is_not_reported(
    scanner, async_db_session, scan_context, add_claim, ownership_entries
):
    """前主人的持有区间已在新主人接手前结束 → 正常转让，不报警。"""
    await add_claim(
        subject_text="李长风",
        subject_entry_id="cx_lee",
        predicate="owns",
        object_type="entity",
        object_entry_id="cx_sword",
        object_value="cx_sword",
        timeline_id="main",
        story_order=10.0,
        valid_from_order=10.0,
        valid_to_order=15.0,
    )
    await add_claim(
        subject_text="王二",
        subject_entry_id="cx_wang",
        predicate="owns",
        object_type="entity",
        object_entry_id="cx_sword",
        object_value="cx_sword",
        timeline_id="main",
        story_order=20.0,
        valid_from_order=20.0,
        fingerprint="fp_wang_owns",
    )

    assert await run_scan(scanner, async_db_session, scan_context) == []


async def test_same_owner_twice_is_not_reported(
    scanner, async_db_session, scan_context, add_claim, ownership_entries
):
    await add_claim(
        subject_text="李长风",
        subject_entry_id="cx_lee",
        predicate="owns",
        object_type="entity",
        object_entry_id="cx_sword",
        object_value="cx_sword",
        timeline_id="main",
        story_order=10.0,
        valid_from_order=10.0,
        valid_to_order=None,
    )
    await add_claim(
        subject_text="李长风",
        subject_entry_id="cx_lee",
        predicate="owns",
        object_type="entity",
        object_entry_id="cx_sword",
        object_value="cx_sword",
        timeline_id="main",
        story_order=20.0,
        valid_from_order=20.0,
        fingerprint="fp_lee_owns_again",
    )

    assert await run_scan(scanner, async_db_session, scan_context) == []


async def test_ownership_conflict_without_valid_from_order_does_not_crash(
    scanner, async_db_session, scan_context, add_claim, ownership_entries
):
    """回归：valid_from_order 为 None 时与数字比较会抛 TypeError。"""
    await add_claim(
        subject_text="李长风",
        subject_entry_id="cx_lee",
        predicate="owns",
        object_type="entity",
        object_entry_id="cx_sword",
        object_value="cx_sword",
        timeline_id="main",
        story_order=10.0,
        valid_from_order=None,
        valid_to_order=None,
    )
    await add_claim(
        subject_text="王二",
        subject_entry_id="cx_wang",
        predicate="owns",
        object_type="entity",
        object_entry_id="cx_sword",
        object_value="cx_sword",
        timeline_id="main",
        story_order=20.0,
        valid_from_order=None,
        fingerprint="fp_wang_owns",
    )

    issue_ids = await run_scan(scanner, async_db_session, scan_context)

    assert len(issue_ids) == 1


async def test_ownership_of_different_items_is_not_reported(
    scanner, async_db_session, scan_context, add_claim, ownership_entries
):
    await add_claim(
        subject_text="李长风",
        subject_entry_id="cx_lee",
        predicate="owns",
        object_type="entity",
        object_entry_id="cx_sword",
        object_value="cx_sword",
        timeline_id="main",
        story_order=10.0,
        valid_from_order=10.0,
    )
    await add_claim(
        subject_text="王二",
        subject_entry_id="cx_wang",
        predicate="owns",
        object_type="entity",
        object_entry_id="cx_shield",
        object_value="cx_shield",
        timeline_id="main",
        story_order=20.0,
        valid_from_order=20.0,
        fingerprint="fp_wang_shield",
    )

    assert await run_scan(scanner, async_db_session, scan_context) == []


# --- 规则 3：知情边界 ---------------------------------------------------------


async def test_knowledge_used_before_acquired_is_reported(
    scanner, async_db_session, scan_context, add_claim
):
    use_claim = await add_claim(
        subject_text="李长风",
        predicate="uses_knowledge",
        object_value="秘密身份",
        chapter_id="ch_a",
        body_rev=2,
        paragraph_id="p_use",
        timeline_id="main",
        story_order=10.0,
    )
    await add_claim(
        subject_text="李长风",
        predicate="acquires_knowledge",
        object_value="秘密身份",
        chapter_id="ch_b",
        timeline_id="main",
        story_order=30.0,
        fingerprint="fp_acquire",
    )

    issue_ids = await run_scan(scanner, async_db_session, scan_context)

    assert len(issue_ids) == 1
    issues = await load_issues(async_db_session)
    assert issues[0].issue_type == "knowledge_boundary"
    assert issues[0].severity == "medium"
    # 锚点指向「提前使用」的那处，作者要改的是这里
    assert issues[0].anchor["claim_id"] == use_claim.id
    assert issues[0].anchor["body_rev"] == 2
    assert issues[0].anchor["pid"] == "p_use"


async def test_knowledge_used_after_acquired_is_not_reported(
    scanner, async_db_session, scan_context, add_claim
):
    await add_claim(
        subject_text="李长风",
        predicate="acquires_knowledge",
        object_value="秘密身份",
        timeline_id="main",
        story_order=10.0,
    )
    await add_claim(
        subject_text="李长风",
        predicate="uses_knowledge",
        object_value="秘密身份",
        timeline_id="main",
        story_order=30.0,
        fingerprint="fp_use",
    )

    assert await run_scan(scanner, async_db_session, scan_context) == []


async def test_knowledge_boundary_is_per_character(
    scanner, async_db_session, scan_context, add_claim
):
    """A 使用、B 获知，不构成 A 的知情越界。"""
    await add_claim(
        subject_text="李长风",
        predicate="uses_knowledge",
        object_value="秘密身份",
        timeline_id="main",
        story_order=10.0,
    )
    await add_claim(
        subject_text="王二",
        predicate="acquires_knowledge",
        object_value="秘密身份",
        timeline_id="main",
        story_order=30.0,
        fingerprint="fp_wang_acquire",
    )

    assert await run_scan(scanner, async_db_session, scan_context) == []


async def test_knowledge_boundary_requires_same_fact(
    scanner, async_db_session, scan_context, add_claim
):
    await add_claim(
        subject_text="李长风",
        predicate="uses_knowledge",
        object_value="秘密身份",
        timeline_id="main",
        story_order=10.0,
    )
    await add_claim(
        subject_text="李长风",
        predicate="acquires_knowledge",
        object_value="另一件事",
        timeline_id="main",
        story_order=30.0,
        fingerprint="fp_other_fact",
    )

    assert await run_scan(scanner, async_db_session, scan_context) == []


# --- 多规则同时命中 -----------------------------------------------------------


async def test_multiple_rules_produce_distinct_issues(
    scanner, async_db_session, scan_context, add_claim
):
    """生死冲突与知情越界同时存在时产生两条不同 issue。"""
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        timeline_id="main",
        story_order=10.0,
    )
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="true",
        timeline_id="main",
        story_order=20.0,
        fingerprint="fp_alive_true",
    )
    await add_claim(
        subject_text="王二",
        predicate="uses_knowledge",
        object_value="秘密",
        timeline_id="main",
        story_order=10.0,
        fingerprint="fp_use",
    )
    await add_claim(
        subject_text="王二",
        predicate="acquires_knowledge",
        object_value="秘密",
        timeline_id="main",
        story_order=30.0,
        fingerprint="fp_acquire",
    )

    issue_ids = await run_scan(scanner, async_db_session, scan_context)

    assert len(issue_ids) == 2
    issues = await load_issues(async_db_session)
    assert {issue.issue_type for issue in issues} == {"alive_conflict", "knowledge_boundary"}
    assert len({issue.fingerprint for issue in issues}) == 2


async def test_returned_issue_ids_are_unique(
    scanner, async_db_session, scan_context, add_claim
):
    """同一冲突被多对 claim 触发时，返回的 id 列表不含重复。"""
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        timeline_id="main",
        story_order=10.0,
    )
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="true",
        timeline_id="main",
        story_order=20.0,
        fingerprint="fp_alive_true",
    )
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        timeline_id="main",
        story_order=30.0,
        fingerprint="fp_dead_again",
    )
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="true",
        timeline_id="main",
        story_order=40.0,
        fingerprint="fp_alive_again",
    )

    issue_ids = await run_scan(scanner, async_db_session, scan_context)

    assert len(issue_ids) == len(set(issue_ids))


# --- 指纹本身 -----------------------------------------------------------------


def test_fingerprint_is_order_independent(scanner):
    first = scanner.compute_issue_fingerprint("alive_conflict", ["a", "b"])
    second = scanner.compute_issue_fingerprint("alive_conflict", ["b", "a"])

    assert first == second


def test_fingerprint_differs_by_issue_type(scanner):
    assert scanner.compute_issue_fingerprint(
        "alive_conflict", ["a"]
    ) != scanner.compute_issue_fingerprint("ownership_conflict", ["a"])


def test_fingerprint_differs_by_identity(scanner):
    assert scanner.compute_issue_fingerprint(
        "alive_conflict", ["cx_lee"]
    ) != scanner.compute_issue_fingerprint("alive_conflict", ["cx_wang"])
