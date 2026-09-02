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

from db.models_consistency_extended import ConsistencyClaim, GuardIssueEvidence
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


async def load_evidence(db, issue_id: str) -> list[GuardIssueEvidence]:
    result = await db.execute(
        select(GuardIssueEvidence)
        .where(GuardIssueEvidence.issue_id == issue_id)
        .order_by(GuardIssueEvidence.sort_order, GuardIssueEvidence.id)
    )
    return list(result.scalars().all())


def issue_snapshot(issue: GuardIssue) -> dict:
    """issue 上「重复扫描不该动」的那些字段。"""
    return {
        "issue_rev": issue.issue_rev,
        "run_id": issue.run_id,
        "status": issue.status,
        "resolved": issue.resolved,
        "resolution": issue.resolution,
        "false_positive": issue.false_positive,
        "stale_at": issue.stale_at,
        "evidence": issue.evidence,
        "anchor": issue.anchor,
        "updated_at": issue.updated_at,
    }


async def test_impact_scan_excludes_unrelated_claims(
    scanner, async_db_session, scan_context, add_claim
):
    await add_claim(subject_text="李长风", predicate="alive", object_value="true")
    await add_claim(
        chapter_id="ch_b",
        subject_text="无关人物",
        predicate="located_at",
        object_value="京城",
        fingerprint="fp_unrelated_location",
    )

    await run_scan(scanner, async_db_session, scan_context)

    assert scanner.last_scan_scope == "impact"
    assert scanner.last_scan_claim_count == 1


async def test_superseded_old_claim_keeps_cross_chapter_impact_recall(
    scanner, async_db_session, scan_context, add_claim
):
    await add_claim(
        status="superseded",
        subject_text="李长风",
        predicate="alive",
        object_value="false",
    )
    await add_claim(
        chapter_id="ch_b",
        subject_text="李长风",
        predicate="alive",
        object_value="true",
        fingerprint="fp_current_alive",
    )

    claims = await scanner._load_impacted_claims(
        async_db_session,
        project_id="proj_a",
        chapter_id="ch_a",
    )

    assert [(claim.chapter_id, claim.status) for claim in claims] == [("ch_b", "accepted")]


async def test_entity_rebinding_keeps_both_old_and_new_impact_sets(
    scanner, async_db_session, scan_context, add_claim, add_entry
):
    await add_entry("ent_old")
    await add_entry("ent_new")
    await add_claim(
        status="superseded",
        subject_text="李长风",
        subject_entry_id="ent_old",
        predicate="alive",
        object_value="false",
        fingerprint="fp_old_binding",
    )
    await add_claim(
        subject_text="李长风",
        subject_entry_id="ent_new",
        predicate="alive",
        object_value="true",
        fingerprint="fp_new_binding",
    )
    await add_claim(
        chapter_id="ch_b",
        subject_text="旧条目关联事实",
        subject_entry_id="ent_old",
        predicate="owns",
        object_value="旧账册",
        fingerprint="fp_old_related",
    )
    await add_claim(
        chapter_id="ch_b",
        subject_text="新条目关联事实",
        subject_entry_id="ent_new",
        predicate="owns",
        object_value="新账册",
        fingerprint="fp_new_related",
    )

    claims = await scanner._load_impacted_claims(
        async_db_session,
        project_id="proj_a",
        chapter_id="ch_a",
    )

    assert {claim.fingerprint for claim in claims} == {
        "fp_new_binding",
        "fp_old_related",
        "fp_new_related",
    }


async def test_empty_impact_basis_falls_back_to_project_scan(
    scanner, async_db_session, scan_context, add_claim
):
    await add_claim(
        chapter_id="ch_b",
        subject_text="李长风",
        predicate="alive",
        object_value="true",
    )

    claims = await scanner._load_impacted_claims(
        async_db_session,
        project_id="proj_a",
        chapter_id="ch_a",
    )

    assert scanner.last_scan_scope == "project"
    assert len(claims) == 1


async def test_knowledge_predicate_family_is_included_in_impact_set(
    scanner, async_db_session, scan_context, add_claim
):
    await add_claim(
        subject_text="李长风",
        predicate="uses_knowledge",
        object_value="密道",
    )
    await add_claim(
        chapter_id="ch_b",
        subject_text="李长风",
        predicate="acquires_knowledge",
        object_value="密道",
        fingerprint="fp_acquires_knowledge",
    )

    claims = await scanner._load_impacted_claims(
        async_db_session,
        project_id="proj_a",
        chapter_id="ch_a",
    )

    assert {claim.predicate for claim in claims} == {
        "uses_knowledge",
        "acquires_knowledge",
    }


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


async def test_rediscovered_issue_bumps_issue_rev_only_when_evidence_changes(
    scanner, async_db_session, scan_context, add_claim
):
    """issue_rev 是「证据换了一版」的信号，不是「又扫了一次」的计数。

    回归：早期实现每次命中都无条件 issue_rev += 1。作者保存正文触发扫描后，他手上
    那个 issue_rev 立刻过期，resolve 接口按乐观锁一律回 409 —— 告警根本没法处置。
    """
    await add_claim(
        subject_text="李长风",
        predicate="alive",
        object_value="false",
        timeline_id="main",
        story_order=10.0,
    )
    alive = await add_claim(
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

    # 同一份实质证据再扫一次：rev 不动
    await run_scan(scanner, async_db_session, scan_context)
    async_db_session.expunge_all()
    issues = await load_issues(async_db_session)
    assert issues[0].issue_rev == 1

    # 证据换了一版（同一条 claim 的来源锚点变了）：rev 递增
    alive = (
        await async_db_session.execute(
            select(ConsistencyClaim).where(ConsistencyClaim.id == alive.id)
        )
    ).scalar_one()
    alive.source_anchor = "P7"
    await async_db_session.flush()

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


# --- 重复扫描的幂等性 ---------------------------------------------------------
#
# 「相同 evidence/anchor/rule_version 的重复扫描必须完全幂等」不是一句口号：作者每
# 保存一次正文就触发一次全项目扫描，如果每次扫描都动 issue 行，作者手上的
# issue_rev 立刻过期（resolve 接口按乐观锁回 409），证据行 id 也会全部漂移。


@pytest.fixture
def alive_conflict(add_claim):
    """建一对「先死后活」的 claim，返回 (dead, alive)。"""

    async def _build(**overrides):
        dead = await add_claim(
            subject_text="李长风",
            predicate="alive",
            object_value="false",
            timeline_id="main",
            story_order=10.0,
            **overrides,
        )
        alive = await add_claim(
            subject_text="李长风",
            predicate="alive",
            object_value="true",
            timeline_id="main",
            story_order=20.0,
            fingerprint="fp_alive_true",
            **overrides,
        )
        return dead, alive

    return _build


async def test_rescanning_identical_evidence_changes_nothing_at_all(
    scanner, async_db_session, scan_context, alive_conflict
):
    """同一份实质证据重复扫描：issue 行的每个字段都保持原值。"""
    await alive_conflict()

    issue_ids = await run_scan(scanner, async_db_session, scan_context)
    async_db_session.expunge_all()
    before = issue_snapshot((await load_issues(async_db_session))[0])
    evidence_before = [(row.id, row.claim_id, row.side) for row in
                       await load_evidence(async_db_session, issue_ids[0])]

    await run_scan(scanner, async_db_session, scan_context)

    async_db_session.expunge_all()
    after = issue_snapshot((await load_issues(async_db_session))[0])
    assert after == before, "重复扫描动了 issue 字段"

    evidence_after = [(row.id, row.claim_id, row.side) for row in
                      await load_evidence(async_db_session, issue_ids[0])]
    # 证据行 id 必须保持不变：之前的实现每次扫描都 DELETE + INSERT，自增主键全部漂移
    assert evidence_after == evidence_before


async def test_rescanning_does_not_bump_issue_rev_when_confidence_is_recomputed(
    scanner, async_db_session, scan_context, alive_conflict
):
    """派生值（claim.confidence）重算不算证据变化。

    只有 claim id / 来源章节 / body_rev / source_anchor 这类能证明「证据换了一版」
    的字段才该触发 rev+1。把 confidence 之类每次抽取都可能微调的值算进去，等于
    每重跑一次抽取就把作者刚处置完的告警全部翻出来。
    """
    dead, _ = await alive_conflict()

    await run_scan(scanner, async_db_session, scan_context)
    async_db_session.expunge_all()
    before = issue_snapshot((await load_issues(async_db_session))[0])

    reloaded = (
        await async_db_session.execute(
            select(ConsistencyClaim).where(ConsistencyClaim.id == dead.id)
        )
    ).scalar_one()
    reloaded.confidence = 0.4321
    await async_db_session.flush()

    await run_scan(scanner, async_db_session, scan_context)

    async_db_session.expunge_all()
    assert issue_snapshot((await load_issues(async_db_session))[0]) == before


# --- resolved / false_positive / stale 的重现规则 ------------------------------


async def test_resolved_issue_stays_resolved_on_identical_evidence(
    scanner, async_db_session, scan_context, alive_conflict
):
    """作者处置过的 issue，同一份证据再扫描保持 resolved。

    resolved 是作者对**这份证据**的判断。凭「规则又检出一次」把它改回 open，
    等于每次保存正文都把已经处理完的告警重新推给作者。
    """
    await alive_conflict()
    await run_scan(scanner, async_db_session, scan_context)

    issues = await load_issues(async_db_session)
    issues[0].status = "resolved"
    issues[0].resolved = True
    issues[0].resolution = "intentional_exception"
    await async_db_session.flush()
    rev_before = issues[0].issue_rev

    await run_scan(scanner, async_db_session, scan_context)

    async_db_session.expunge_all()
    refreshed = (await load_issues(async_db_session))[0]
    assert refreshed.status == "resolved"
    assert refreshed.resolved is True
    assert refreshed.resolution == "intentional_exception"
    assert refreshed.issue_rev == rev_before


async def test_resolved_issue_reopens_when_the_evidence_version_changes(
    scanner, async_db_session, scan_context, alive_conflict
):
    """证据换了一版：重开并 rev+1，作者的旧处置不再适用于新的 rev。"""
    _, alive = await alive_conflict()
    await run_scan(scanner, async_db_session, scan_context)

    issues = await load_issues(async_db_session)
    issues[0].status = "resolved"
    issues[0].resolved = True
    issues[0].resolution = "accept_new_fact"
    await async_db_session.flush()
    rev_before = issues[0].issue_rev

    reloaded = (
        await async_db_session.execute(
            select(ConsistencyClaim).where(ConsistencyClaim.id == alive.id)
        )
    ).scalar_one()
    reloaded.body_rev = 2
    await async_db_session.flush()

    await run_scan(scanner, async_db_session, scan_context, body_rev=2)

    async_db_session.expunge_all()
    refreshed = (await load_issues(async_db_session))[0]
    assert refreshed.status == "open"
    assert refreshed.resolved is False
    assert refreshed.resolution is None
    assert refreshed.issue_rev == rev_before + 1


async def test_false_positive_evidence_is_not_silently_replaced(
    scanner, async_db_session, scan_context, alive_conflict
):
    """误报 issue 在不增 rev 的情况下不得被换掉证据。

    回归：旧实现对 false_positive 分支无条件覆盖 anchor/evidence 且不动 issue_rev。
    作者看到的还是他判过的那个 rev，底下的证据却已经换成另一版正文的 —— 他的
    「这是误报」等于被悄悄套用到了一份他没看过的证据上。
    """
    await alive_conflict()
    await run_scan(scanner, async_db_session, scan_context)

    issues = await load_issues(async_db_session)
    issues[0].status = "false_positive"
    issues[0].false_positive = True
    issues[0].resolution = "false_positive"
    await async_db_session.flush()

    # 重新读一次再取快照：updated_at 带 onupdate=func.now()，flush 之后是过期状态，
    # 直接读会触发同步惰性加载
    async_db_session.expunge_all()
    before = issue_snapshot((await load_issues(async_db_session))[0])

    await run_scan(scanner, async_db_session, scan_context)

    async_db_session.expunge_all()
    assert issue_snapshot((await load_issues(async_db_session))[0]) == before


async def test_false_positive_stays_false_positive_but_tracks_new_evidence_with_a_new_rev(
    scanner, async_db_session, scan_context, alive_conflict
):
    """误报永不复活；但证据换版时必须连 issue_rev 一起更新。"""
    _, alive = await alive_conflict()
    await run_scan(scanner, async_db_session, scan_context)

    issues = await load_issues(async_db_session)
    issues[0].status = "false_positive"
    issues[0].false_positive = True
    issues[0].resolution = "false_positive"
    await async_db_session.flush()
    rev_before = issues[0].issue_rev
    evidence_before = issues[0].evidence

    reloaded = (
        await async_db_session.execute(
            select(ConsistencyClaim).where(ConsistencyClaim.id == alive.id)
        )
    ).scalar_one()
    reloaded.source_anchor = "P9"
    await async_db_session.flush()

    await run_scan(scanner, async_db_session, scan_context)

    async_db_session.expunge_all()
    refreshed = (await load_issues(async_db_session))[0]
    assert refreshed.status == "false_positive", "误报不得复活"
    assert refreshed.false_positive is True
    assert refreshed.resolved is False or refreshed.resolution == "false_positive"
    assert refreshed.issue_rev == rev_before + 1, "换了证据就必须换 rev"
    assert refreshed.evidence != evidence_before


async def test_stale_issue_reopens_without_a_rev_bump_when_the_evidence_is_unchanged(
    scanner, async_db_session, scan_context, alive_conflict
):
    """stale 重现同样按「证据是否变化」判 rev。

    stale 是系统的观察（这次没检出），不是作者的决定，所以冲突回来就该重开；
    但证据没换版时 rev 不能动，否则作者手上的 rev 又过期了。
    """
    dead, _ = await alive_conflict()
    await run_scan(scanner, async_db_session, scan_context)
    rev_before = (await load_issues(async_db_session))[0].issue_rev

    dead.status = "superseded"
    await async_db_session.flush()
    await run_scan(scanner, async_db_session, scan_context)
    async_db_session.expunge_all()
    assert (await load_issues(async_db_session))[0].status == "stale"

    dead = (
        await async_db_session.execute(
            select(ConsistencyClaim).where(ConsistencyClaim.id == dead.id)
        )
    ).scalar_one()
    dead.status = "accepted"
    await async_db_session.flush()
    await run_scan(scanner, async_db_session, scan_context)

    async_db_session.expunge_all()
    refreshed = (await load_issues(async_db_session))[0]
    assert refreshed.status == "open"
    assert refreshed.stale_at is None
    assert refreshed.issue_rev == rev_before, "证据没变，stale 重开不该涨 rev"


async def test_stale_issue_reopens_with_a_rev_bump_when_the_evidence_changed(
    scanner, async_db_session, scan_context, alive_conflict
):
    """冲突以另一版证据回来：重开且 rev+1。"""
    dead, _ = await alive_conflict()
    await run_scan(scanner, async_db_session, scan_context)
    rev_before = (await load_issues(async_db_session))[0].issue_rev

    dead.status = "superseded"
    await async_db_session.flush()
    await run_scan(scanner, async_db_session, scan_context)

    dead.status = "accepted"
    dead.source_anchor = "P11"
    await async_db_session.flush()
    await run_scan(scanner, async_db_session, scan_context)

    async_db_session.expunge_all()
    refreshed = (await load_issues(async_db_session))[0]
    assert refreshed.status == "open"
    assert refreshed.stale_at is None
    assert refreshed.issue_rev == rev_before + 1


async def test_a_cross_chapter_issue_goes_stale_when_the_cited_chapter_is_fixed(
    scanner, async_db_session, scan_context, add_claim
):
    """跨章冲突的锚点在 ch_b，作者改 ch_a 解决它 —— 扫 ch_a 必须能把它标 stale。

    回归：stale 判定曾按 GuardIssue.chapter_id == 被扫章节过滤。跨章冲突的
    chapter_id 是后出现的那一章（ch_b），而作者是在 ch_a 把「死亡」那句删掉的；
    只按锚点章节过滤，这条 issue 永远停在 open。
    """
    dead = await add_claim(
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
        timeline_id="main",
        story_order=20.0,
        fingerprint="fp_alive_true_b",
    )
    await run_scan(scanner, async_db_session, scan_context)

    async_db_session.expunge_all()
    issue = (await load_issues(async_db_session))[0]
    assert issue.chapter_id == "ch_b", "前提：issue 归属在后出现的那一章"

    dead = (
        await async_db_session.execute(
            select(ConsistencyClaim).where(ConsistencyClaim.id == dead.id)
        )
    ).scalar_one()
    dead.status = "superseded"
    await async_db_session.flush()

    await run_scan(scanner, async_db_session, scan_context, chapter_id="ch_a")

    async_db_session.expunge_all()
    refreshed = (await load_issues(async_db_session))[0]
    assert refreshed.status == "stale"
    assert refreshed.stale_at is not None


# --- run_id 与「只更新受影响 issue」 -------------------------------------------


async def test_an_unchanged_issue_keeps_the_run_that_actually_found_it(
    scanner, async_db_session, scan_context, alive_conflict, make_run
):
    """扫描是全项目的，但 run_id 只能记在真被本次扫描改动的 issue 上。

    回归：旧实现把每次扫描检出的**所有** issue 的 run_id 改成本章的 run。扫第 900
    章会把第 3 章的告警重新挂到第 900 章的 run 上，run 与 issue 的对应关系失去意义，
    「这条告警是哪次运行发现的」再也答不出来。
    """
    await alive_conflict()
    await run_scan(scanner, async_db_session, scan_context)

    async_db_session.expunge_all()
    issue = (await load_issues(async_db_session))[0]
    first_run_id = issue.run_id
    assert first_run_id == scan_context.id

    later_run = make_run(project_id="proj_a", chapter_id="ch_b", body_rev=1)
    async_db_session.add(later_run)
    await async_db_session.flush()

    # 扫另一章：同一个冲突会被再次检出（规则读全项目 claim），但证据没变
    await run_scan(scanner, async_db_session, later_run, chapter_id="ch_b")

    async_db_session.expunge_all()
    refreshed = (await load_issues(async_db_session))[0]
    assert refreshed.run_id == first_run_id, "未变化的 issue 不该被改挂到别的 run"
    assert refreshed.issue_rev == 1


async def test_a_changed_issue_moves_to_the_run_that_re_detected_it(
    scanner, async_db_session, scan_context, alive_conflict, make_run
):
    """证据真的变了：issue 挂到重新检出它的那个 run。"""
    _, alive = await alive_conflict()
    await run_scan(scanner, async_db_session, scan_context)

    later_run = make_run(project_id="proj_a", chapter_id="ch_b", body_rev=1)
    async_db_session.add(later_run)
    await async_db_session.flush()

    reloaded = (
        await async_db_session.execute(
            select(ConsistencyClaim).where(ConsistencyClaim.id == alive.id)
        )
    ).scalar_one()
    reloaded.source_anchor = "P13"
    await async_db_session.flush()

    await run_scan(scanner, async_db_session, later_run, chapter_id="ch_b")

    async_db_session.expunge_all()
    refreshed = (await load_issues(async_db_session))[0]
    assert refreshed.run_id == later_run.id
    assert refreshed.issue_rev == 2


async def test_repeated_hits_of_one_conflict_do_not_bump_rev_twice_per_scan(
    scanner, async_db_session, scan_context, add_claim
):
    """同一指纹在一次扫描里命中多次时只写一次。

    回归：「死—活—死—活」产生两对证据 claim，指纹相同。逐条 upsert 会让同一行先被
    第一对写、再被第二对覆盖，下一次扫描又反向覆盖一遍 —— issue_rev 每扫一次涨两级，
    而且两次扫描之间证据来回抖动，永远达不到幂等。
    """
    for order, value, fingerprint in (
        (10.0, "false", "fp_dead_1"),
        (20.0, "true", "fp_alive_1"),
        (30.0, "false", "fp_dead_2"),
        (40.0, "true", "fp_alive_2"),
    ):
        await add_claim(
            subject_text="李长风",
            predicate="alive",
            object_value=value,
            timeline_id="main",
            story_order=order,
            fingerprint=fingerprint,
        )

    issue_ids = await run_scan(scanner, async_db_session, scan_context)
    assert len(issue_ids) == 1

    async_db_session.expunge_all()
    before = issue_snapshot((await load_issues(async_db_session))[0])
    assert before["issue_rev"] == 1
    # 两次命中的证据 claim 合并进同一条 issue，后面几对证据不丢
    assert len(before["evidence"]["claim_ids"]) == 4

    await run_scan(scanner, async_db_session, scan_context)

    async_db_session.expunge_all()
    assert issue_snapshot((await load_issues(async_db_session))[0]) == before


# --- 唯一键并发竞态 -----------------------------------------------------------


async def test_a_concurrent_insert_of_the_same_fingerprint_is_absorbed(
    scanner, async_db_session, scan_context, alive_conflict
):
    """uq_guard_issue_fingerprint 上的「先查后插」竞态由 SAVEPOINT 兜住。

    模拟真实竞态：本 worker 查完发现没有，另一个 worker 在它插入之前先插进去了。
    这里在「查完之后、插入之前」注入那一行 —— 撞键后必须回滚 SAVEPOINT 并改用
    已存在的那条 issue，而不是把整个扫描打成 IntegrityError（那会让本次扫描已经
    写好的其他告警一起丢掉）。
    """
    await alive_conflict()

    real_select = scanner._select_issue
    calls = {"n": 0}

    async def racing_select(db, project_id, fingerprint):
        found = await real_select(db, project_id, fingerprint)
        calls["n"] += 1
        if calls["n"] == 1 and found is None:
            db.add(
                GuardIssue(
                    id="gi_raced",
                    project_id=project_id,
                    chapter_id="ch_a",
                    run_id=scan_context.id,
                    issue_type="alive_conflict",
                    rule_version="1.0.0",
                    fingerprint=fingerprint,
                    severity="high",
                    confidence=0.95,
                    description="另一个 worker 先插入的同一条冲突",
                    evidence={},
                    anchor={},
                    actions=["false_positive"],
                    status="open",
                    issue_rev=1,
                    resolved=False,
                    false_positive=False,
                )
            )
            await db.flush()
        return found

    scanner._select_issue = racing_select

    issue_ids = await run_scan(scanner, async_db_session, scan_context)

    assert issue_ids == ["gi_raced"], "撞键后必须复用已存在的 issue"
    async_db_session.expunge_all()
    issues = await load_issues(async_db_session)
    assert len(issues) == 1, "竞态不得产生两条 issue"
    # 抢先插入的那行证据是空的，本 worker 必须把真实证据补上
    assert issues[0].evidence["claim_ids"]
    assert len(await load_evidence(async_db_session, "gi_raced")) == 2


async def test_a_concurrent_resolution_is_not_overwritten_by_the_racing_scan(
    scanner, async_db_session, scan_context, alive_conflict
):
    """竞态读回的是作者已判误报的那一行时，扫描不得把它改回 open。"""
    await alive_conflict()

    real_select = scanner._select_issue
    calls = {"n": 0}

    async def racing_select(db, project_id, fingerprint):
        found = await real_select(db, project_id, fingerprint)
        calls["n"] += 1
        if calls["n"] == 1 and found is None:
            db.add(
                GuardIssue(
                    id="gi_raced_fp",
                    project_id=project_id,
                    chapter_id="ch_a",
                    run_id=scan_context.id,
                    issue_type="alive_conflict",
                    rule_version="1.0.0",
                    fingerprint=fingerprint,
                    severity="high",
                    confidence=0.95,
                    description="并发插入后立刻被判误报",
                    evidence={},
                    anchor={},
                    actions=["false_positive"],
                    status="false_positive",
                    issue_rev=2,
                    resolved=True,
                    resolution="false_positive",
                    false_positive=True,
                )
            )
            await db.flush()
        return found

    scanner._select_issue = racing_select

    await run_scan(scanner, async_db_session, scan_context)

    async_db_session.expunge_all()
    issues = await load_issues(async_db_session)
    assert len(issues) == 1
    assert issues[0].status == "false_positive"
    assert issues[0].false_positive is True


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


async def test_knowledge_boundary_requires_the_same_timeline(
    scanner, async_db_session, scan_context, add_claim
):
    """不同 timeline 的知情状态互不影响。

    回归：旧实现完全不看 timeline_id。回忆线/平行线里的「获知」被拿来和主线的
    「使用」比顺序 —— 一段回忆的 story_order 排在主线之后，主线里正常的用法就被
    报成知情越界。生死冲突与归属冲突都按 timeline 分组，知情边界不能是例外。
    """
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
        object_value="秘密身份",
        timeline_id="flashback",
        story_order=30.0,
        fingerprint="fp_acquire_flashback",
    )

    assert await run_scan(scanner, async_db_session, scan_context) == []


async def test_knowledge_boundary_does_not_compare_claims_without_a_timeline(
    scanner, async_db_session, scan_context, add_claim
):
    """timeline 为空时不比较。

    timeline_id 是 NULL 意味着这条事实归属哪条叙事线还没定（抽取没能判断）。
    把两条「不知道属于哪条线」的 claim 拿来比先后，得到的告警没有依据 ——
    宁可漏报也不能让作者去核对一条系统自己都说不清前提的告警。
    """
    await add_claim(
        subject_text="李长风",
        predicate="uses_knowledge",
        object_value="秘密身份",
        timeline_id=None,
        story_order=10.0,
    )
    await add_claim(
        subject_text="李长风",
        predicate="acquires_knowledge",
        object_value="秘密身份",
        timeline_id=None,
        story_order=30.0,
        fingerprint="fp_acquire_no_timeline",
    )

    assert await run_scan(scanner, async_db_session, scan_context) == []


async def test_knowledge_boundary_does_not_compare_a_null_timeline_against_a_named_one(
    scanner, async_db_session, scan_context, add_claim
):
    """一边有 timeline、一边为空：同样不比较。"""
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
        object_value="秘密身份",
        timeline_id=None,
        story_order=30.0,
        fingerprint="fp_acquire_null_timeline",
    )

    assert await run_scan(scanner, async_db_session, scan_context) == []


async def test_knowledge_boundary_is_still_reported_within_a_secondary_timeline(
    scanner, async_db_session, scan_context, add_claim
):
    """timeline 相同就照常检测，不是只认 main。"""
    await add_claim(
        subject_text="李长风",
        predicate="uses_knowledge",
        object_value="秘密身份",
        timeline_id="flashback",
        story_order=10.0,
    )
    await add_claim(
        subject_text="李长风",
        predicate="acquires_knowledge",
        object_value="秘密身份",
        timeline_id="flashback",
        story_order=30.0,
        fingerprint="fp_acquire_same_flashback",
    )

    issue_ids = await run_scan(scanner, async_db_session, scan_context)

    assert len(issue_ids) == 1
    issues = await load_issues(async_db_session)
    assert issues[0].issue_type == "knowledge_boundary"
    # 指纹里带的是这条线的 timeline，不是 main
    assert issues[0].fingerprint == scanner.compute_issue_fingerprint(
        "knowledge_boundary",
        ["text:李长风", "knowledge:秘密身份", "timeline:flashback"],
    )


async def test_knowledge_boundary_fingerprints_differ_per_timeline(
    scanner, async_db_session, scan_context
):
    """同一主体、同一知识、不同 timeline ⇒ 两个独立指纹。

    否则两条叙事线上的同名问题会合并成一条 issue，作者处置了一条就把另一条也
    「处置」掉了。
    """
    main_fp = scanner.compute_issue_fingerprint(
        "knowledge_boundary", ["text:李长风", "knowledge:秘密身份", "timeline:main"]
    )
    flashback_fp = scanner.compute_issue_fingerprint(
        "knowledge_boundary", ["text:李长风", "knowledge:秘密身份", "timeline:flashback"]
    )
    assert main_fp != flashback_fp


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
