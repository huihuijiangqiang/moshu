"""
一致性 API 的行为测试：issue 处置的乐观并发、动作校验与处置记录落库。
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from db.models_consistency import OutboxEvent
from db.models_consistency_extended import GuardIssueEvidence, GuardResolution
from db.models_core import ChapterBody
from db.models_guard import GuardIssue


def build_issue(run_id: int, *, issue_id: str = "gi_1", issue_rev: int = 1) -> GuardIssue:
    """按真实 GuardIssue 字段构造：issue_type/description/evidence/anchor/actions。"""
    return GuardIssue(
        id=issue_id,
        project_id="proj_a",
        chapter_id="ch_a",
        run_id=run_id,
        issue_type="alive_conflict",
        rule_version="1.0.0",
        fingerprint=f"fp_{issue_id}",
        severity="high",
        confidence=0.95,
        description="角色先死亡后复活",
        evidence={},
        anchor={"pid": "p1"},
        actions=["accept_old_fact", "accept_new_fact"],
        status="open",
        issue_rev=issue_rev,
        resolved=False,
        false_positive=False,
    )


async def seed_issue(session, seed_project, make_run, **issue_kwargs):
    """建好 user/project/chapter 后，再按外键顺序写入 run 与 issue。"""
    await seed_project()
    run = make_run(project_id="proj_a", chapter_id="ch_a")
    session.add(run)
    await session.flush()
    issue = build_issue(run.id, **issue_kwargs)
    session.add(issue)
    await session.commit()
    return issue


async def test_resolve_issue_persists_resolution_and_bumps_rev(
    app_client, async_db_session, seed_project, make_run, auth_headers
):
    """处置成功：issue_rev +1，并写入一条绑定旧 issue_rev 的 GuardResolution。"""
    await seed_issue(async_db_session, seed_project, make_run)

    response = await app_client.post(
        "/consistency/issues/proj_a/gi_1/resolve",
        json={"action": "accept_new_fact", "note": "作者确认", "issue_rev": 1},
        headers=auth_headers("user_a"),
    )

    assert response.status_code == 200
    assert response.json()["new_issue_rev"] == 2

    issue = (
        await async_db_session.execute(select(GuardIssue).where(GuardIssue.id == "gi_1"))
    ).scalar_one()
    assert issue.issue_rev == 2
    assert issue.status == "resolved"
    assert issue.resolved is True
    assert issue.resolution == "accept_new_fact"

    resolution = (
        await async_db_session.execute(
            select(GuardResolution).where(GuardResolution.issue_id == "gi_1")
        )
    ).scalar_one()
    # created_by 是真实字段名（不是 user_id）；issue_rev 记录被处置的那一版
    assert resolution.created_by == "user_a"
    assert resolution.issue_rev == 1
    assert resolution.action == "accept_new_fact"


async def test_resolve_issue_rejects_stale_issue_rev(
    app_client, async_db_session, seed_project, make_run, auth_headers
):
    """issue_rev 不匹配 → 409，且不写处置记录。"""
    await seed_issue(async_db_session, seed_project, make_run, issue_rev=3)

    response = await app_client.post(
        "/consistency/issues/proj_a/gi_1/resolve",
        json={"action": "accept_new_fact", "issue_rev": 1},
        headers=auth_headers("user_a"),
    )

    assert response.status_code == 409

    resolutions = (
        await async_db_session.execute(
            select(GuardResolution).where(GuardResolution.issue_id == "gi_1")
        )
    ).scalars().all()
    assert resolutions == []


async def test_second_concurrent_resolve_is_rejected(
    app_client, async_db_session, seed_project, make_run, auth_headers
):
    """同一 issue_rev 重复处置：第一次 200，第二次 409（乐观锁生效）。"""
    await seed_issue(async_db_session, seed_project, make_run)

    payload = {"action": "defer", "issue_rev": 1}
    first = await app_client.post(
        "/consistency/issues/proj_a/gi_1/resolve", json=payload, headers=auth_headers("user_a")
    )
    second = await app_client.post(
        "/consistency/issues/proj_a/gi_1/resolve", json=payload, headers=auth_headers("user_a")
    )

    assert first.status_code == 200
    assert second.status_code == 409


async def test_resolve_issue_rejects_action_outside_check_constraint(
    app_client, async_db_session, seed_project, make_run, auth_headers
):
    """非法 action 在入口就被拒（422），不会撞到数据库 CHECK 约束。"""
    await seed_issue(async_db_session, seed_project, make_run)

    response = await app_client.post(
        "/consistency/issues/proj_a/gi_1/resolve",
        json={"action": "keep-old", "issue_rev": 1},
        headers=auth_headers("user_a"),
    )

    assert response.status_code == 422


async def test_false_positive_action_sets_false_positive_state(
    app_client, async_db_session, seed_project, make_run, auth_headers
):
    """action=false_positive 时状态落到 false_positive 而非 resolved。"""
    await seed_issue(async_db_session, seed_project, make_run)

    response = await app_client.post(
        "/consistency/issues/proj_a/gi_1/resolve",
        json={"action": "false_positive", "issue_rev": 1},
        headers=auth_headers("user_a"),
    )

    assert response.status_code == 200
    issue = (
        await async_db_session.execute(select(GuardIssue).where(GuardIssue.id == "gi_1"))
    ).scalar_one()
    assert issue.status == "false_positive"
    assert issue.false_positive is True


async def test_resolve_missing_issue_returns_404(
    app_client, async_db_session, seed_project, auth_headers
):
    """issue 不存在 → 404（与 409 区分开）。"""
    await seed_project()
    await async_db_session.commit()

    response = await app_client.post(
        "/consistency/issues/proj_a/gi_missing/resolve",
        json={"action": "defer", "issue_rev": 1},
        headers=auth_headers("user_a"),
    )

    assert response.status_code == 404


async def test_resolve_is_denied_across_tenants(
    app_client, async_db_session, seed_project, make_run, make_user, auth_headers
):
    """他人无法处置本项目的 issue → 403，且状态不变。"""
    await seed_issue(async_db_session, seed_project, make_run)
    async_db_session.add(make_user("user_intruder"))
    await async_db_session.commit()

    response = await app_client.post(
        "/consistency/issues/proj_a/gi_1/resolve",
        json={"action": "defer", "issue_rev": 1},
        headers=auth_headers("user_intruder"),
    )

    assert response.status_code == 403
    issue = (
        await async_db_session.execute(select(GuardIssue).where(GuardIssue.id == "gi_1"))
    ).scalar_one()
    assert issue.status == "open"


async def test_project_scan_queues_current_body_and_overview_exposes_runtime(
    app_client, async_db_session, seed_project, auth_headers
):
    await seed_project()
    async_db_session.add(
        ChapterBody(chapter_id="ch_a", content_html="<p>正文</p>", content_json={"type": "doc"}, rev=2)
    )
    await async_db_session.commit()

    response = await app_client.post(
        "/consistency/projects/proj_a/scan", headers=auth_headers("user_a")
    )
    assert response.status_code == 200
    assert response.json()["queued"] == 1

    event = (
        await async_db_session.execute(
            select(OutboxEvent).where(OutboxEvent.topic == "consistency.manual_scan")
        )
    ).scalar_one()
    assert event.payload["project_id"] == "proj_a"
    assert event.payload["body_rev"] == 2

    overview = await app_client.get(
        "/consistency/projects/proj_a/overview", headers=auth_headers("user_a")
    )
    assert overview.status_code == 200
    body = overview.json()
    assert body["status"] == "queued"
    assert body["queued"] == 1
    assert body["outbox_pending"] == 1
    assert body["runs"][0]["chapter_id"] == "ch_a"
    assert body["runs"][0]["body_rev"] == 2


async def test_project_scan_and_overview_are_denied_across_tenants(
    app_client, async_db_session, seed_project, make_user, auth_headers
):
    await seed_project()
    async_db_session.add(make_user("user_intruder"))
    await async_db_session.commit()

    scan = await app_client.post(
        "/consistency/projects/proj_a/scan", headers=auth_headers("user_intruder")
    )
    overview = await app_client.get(
        "/consistency/projects/proj_a/overview", headers=auth_headers("user_intruder")
    )
    assert scan.status_code == 403
    assert overview.status_code == 403


async def test_project_scan_resets_failed_current_revision_for_retry(
    app_client, async_db_session, seed_project, make_run, auth_headers
):
    await seed_project()
    async_db_session.add(
        ChapterBody(chapter_id="ch_a", content_html="<p>正文</p>", content_json={"type": "doc"}, rev=2)
    )
    run = make_run(
        project_id="proj_a",
        chapter_id="ch_a",
        body_rev=2,
        status="failed",
        extract_state="failed",
        summary_state="pending",
        scan_state="pending",
        error_code="extraction_error",
        error_detail="gateway overloaded",
    )
    async_db_session.add(run)
    await async_db_session.commit()

    response = await app_client.post(
        "/consistency/projects/proj_a/scan", headers=auth_headers("user_a")
    )
    assert response.status_code == 200
    await async_db_session.refresh(run)
    assert run.status == "pending"
    assert run.extract_state == "pending"
    assert run.error_code is None
    assert run.error_detail is None

    repeated = await app_client.post(
        "/consistency/projects/proj_a/scan", headers=auth_headers("user_a")
    )
    assert repeated.status_code == 200
    assert repeated.json()["queued"] == 0, "fresh pending run must not be queued twice"


async def test_project_scan_only_recovers_active_runs_after_the_worker_hard_limit(
    app_client, async_db_session, seed_project, make_run, auth_headers
):
    await seed_project()
    async_db_session.add(
        ChapterBody(chapter_id="ch_a", content_html="<p>正文</p>", content_json={"type": "doc"}, rev=2)
    )
    run = make_run(
        project_id="proj_a",
        chapter_id="ch_a",
        body_rev=2,
        status="extracting",
        extract_state="running",
        summary_state="pending",
        scan_state="pending",
    )
    run.updated_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    async_db_session.add(run)
    await async_db_session.commit()

    still_valid = await app_client.post(
        "/consistency/projects/proj_a/scan", headers=auth_headers("user_a")
    )
    assert still_valid.status_code == 200
    assert still_valid.json()["queued"] == 0

    run.updated_at = datetime.now(timezone.utc) - timedelta(minutes=32)
    await async_db_session.commit()
    recovered = await app_client.post(
        "/consistency/projects/proj_a/scan", headers=auth_headers("user_a")
    )
    assert recovered.status_code == 200
    assert recovered.json()["queued"] == 1
    await async_db_session.refresh(run)
    assert run.status == "pending"
    assert run.extract_state == "pending"


async def test_issue_list_includes_chapter_evidence_actions_and_revision(
    app_client, async_db_session, seed_project, make_run, auth_headers
):
    issue = await seed_issue(async_db_session, seed_project, make_run)
    async_db_session.add(
        GuardIssueEvidence(
            issue_id=issue.id,
            side="actual",
            source_kind="body",
            chapter_id="ch_a",
            body_rev=1,
            paragraph_id="p-2",
            quote="她亲眼看见已经死去的人推门而入。",
            sort_order=1,
        )
    )
    await async_db_session.commit()

    response = await app_client.get(
        "/consistency/issues/proj_a", headers=auth_headers("user_a")
    )
    assert response.status_code == 200
    row = response.json()[0]
    assert row["chapter_index"] == 1024
    assert row["chapter_title"] == "Chapter ch_a"
    assert row["issue_rev"] == 1
    assert row["actions"] == ["accept_old_fact", "accept_new_fact"]
    assert row["evidence"] == [{
        "label": "本次正文",
        "text": "她亲眼看见已经死去的人推门而入。",
        "accent": True,
        "chapter_id": "ch_a",
        "paragraph_id": "p-2",
    }]


async def test_status_endpoint_uses_shared_pipeline_version(
    app_client, async_db_session, seed_project, make_run, auth_headers
):
    """状态查询的默认 pipeline_version 必须与写入端一致，否则永远 404。"""
    from services.consistency import PIPELINE_VERSION

    await seed_project()
    async_db_session.add(
        make_run(project_id="proj_a", chapter_id="ch_a", body_rev=2, pipeline_version=PIPELINE_VERSION)
    )
    await async_db_session.commit()

    response = await app_client.get(
        "/consistency/status/ch_a/2", headers=auth_headers("user_a")
    )

    assert response.status_code == 200
    assert response.json()["pipeline_version"] == PIPELINE_VERSION


async def test_status_endpoint_exposes_each_phase_separately(
    app_client, async_db_session, seed_project, make_run, auth_headers
):
    """扫描完成、摘要仍在跑：status 说不清，phases 必须说清。

    单个 status 列表达不了并行支线。前端要判断「摘要能不能用」只能看 phases.summary；
    只暴露 status 的话，scanning 既可能是「摘要没开始」也可能是「摘要已好」。
    """
    from services.consistency import PIPELINE_VERSION

    await seed_project()
    async_db_session.add(
        make_run(
            project_id="proj_a",
            chapter_id="ch_a",
            body_rev=2,
            pipeline_version=PIPELINE_VERSION,
            status="scanning",
            extract_state="succeeded",
            summary_state="running",
            scan_state="succeeded",
        )
    )
    await async_db_session.commit()

    response = await app_client.get(
        "/consistency/status/ch_a/2", headers=auth_headers("user_a")
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "scanning"
    assert body["phases"] == {
        "extract": "succeeded",
        "summary": "running",
        "scan": "succeeded",
    }


async def test_status_endpoint_reports_the_root_cause_of_a_failure(
    app_client, async_db_session, seed_project, make_run, auth_headers
):
    """失败时把首错的 code 与 detail 都交出去，并指明是哪一条支线失败的。"""
    from services.consistency import PIPELINE_VERSION

    await seed_project()
    async_db_session.add(
        make_run(
            project_id="proj_a",
            chapter_id="ch_a",
            body_rev=2,
            pipeline_version=PIPELINE_VERSION,
            status="failed",
            extract_state="succeeded",
            summary_state="failed",
            scan_state="succeeded",
            error_code="summary_error",
            error_detail="summary gateway down",
        )
    )
    await async_db_session.commit()

    response = await app_client.get(
        "/consistency/status/ch_a/2", headers=auth_headers("user_a")
    )

    body = response.json()
    assert body["error_code"] == "summary_error"
    assert body["error_detail"] == "summary gateway down"
    assert body["phases"]["summary"] == "failed", "看得出是哪一条支线拖垮了 run"
    assert body["phases"]["scan"] == "succeeded"
