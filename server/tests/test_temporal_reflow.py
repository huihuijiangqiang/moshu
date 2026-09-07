from sqlalchemy import select

from db.models_consistency import OutboxEvent
from db.models_consistency_extended import ConsistencyClaim
from db.models_core import ChapterBody
from services.temporal_reflow import enqueue_temporal_rescans, reflow_project_timeline
from services.timeline import parse_absolute_anchor


async def _seed_claims(session, seed_project, make_claim):
    await seed_project(chapter_ids=("ch_a", "ch_b", "ch_c"))
    root = make_claim(
        project_id="proj_a",
        chapter_id="ch_a",
        fingerprint="root",
        temporal_event_ref="启程",
        timeline_id="main",
        order_basis="absolute_datetime",
        order_confidence=0.95,
        temporal_anchor_text="2026-01-01",
        temporal_anchor_value="2026-01-01",
        story_order=None,
    )
    next_day = make_claim(
        project_id="proj_a",
        chapter_id="ch_b",
        fingerprint="next-day",
        temporal_event_ref="抵达",
        temporal_relation="after",
        temporal_relation_ref="启程",
        temporal_anchor_text="次日",
        timeline_id="main",
        order_basis="relative_to_anchor",
        order_confidence=0.95,
        story_order=123,
    )
    fuzzy = make_claim(
        project_id="proj_a",
        chapter_id="ch_c",
        fingerprint="fuzzy",
        temporal_event_ref="开市",
        temporal_relation="after",
        temporal_relation_ref="抵达",
        temporal_anchor_text="过几日后",
        timeline_id="main",
        order_basis="relative_to_anchor",
        order_confidence=0.95,
        story_order=456,
    )
    session.add_all([root, next_day, fuzzy])
    await session.commit()
    return root, next_day, fuzzy


async def test_project_reflow_cascades_exact_dependencies_and_preserves_fuzzy_ranges(
    async_db_session, seed_project, make_claim
):
    root, next_day, fuzzy = await _seed_claims(
        async_db_session, seed_project, make_claim
    )

    result = await reflow_project_timeline(async_db_session, project_id="proj_a")
    await async_db_session.commit()
    await async_db_session.refresh(root)
    await async_db_session.refresh(next_day)
    await async_db_session.refresh(fuzzy)

    root_order = parse_absolute_anchor("2026-01-01")
    assert float(root.story_order) == root_order
    assert float(next_day.story_order) == root_order + 86400
    assert next_day.temporal_resolution["dependency_status"] == "resolved"
    assert next_day.temporal_resolution["normalized"] == "1日后"
    assert fuzzy.story_order is None
    assert fuzzy.temporal_resolution["dependency_status"] == "unresolved"
    assert fuzzy.temporal_resolution["offset_min_seconds"] == 2 * 86400
    assert fuzzy.temporal_resolution["offset_max_seconds"] == 7 * 86400
    assert result.claims_examined == 3
    assert result.claims_changed == 3
    assert result.affected_chapter_ids == ["ch_a", "ch_b", "ch_c"]
    assert result.resolved == 1
    assert result.unresolved == 1


async def test_project_reflow_is_idempotent(async_db_session, seed_project, make_claim):
    await _seed_claims(async_db_session, seed_project, make_claim)
    await reflow_project_timeline(async_db_session, project_id="proj_a")
    await async_db_session.commit()

    repeated = await reflow_project_timeline(async_db_session, project_id="proj_a")
    assert repeated.claims_changed == 0
    assert repeated.affected_chapter_ids == []


async def test_project_reflow_marks_cycles_without_assigning_order(
    async_db_session, seed_project, make_claim
):
    await seed_project(chapter_ids=("ch_a", "ch_b"))
    async_db_session.add_all(
        [
            make_claim(
                project_id="proj_a",
                chapter_id="ch_a",
                fingerprint="cycle-a",
                temporal_event_ref="甲",
                temporal_relation="after",
                temporal_relation_ref="乙",
                temporal_anchor_text="一日后",
                timeline_id="main",
                order_basis="relative_to_anchor",
                order_confidence=0.95,
            ),
            make_claim(
                project_id="proj_a",
                chapter_id="ch_b",
                fingerprint="cycle-b",
                temporal_event_ref="乙",
                temporal_relation="after",
                temporal_relation_ref="甲",
                temporal_anchor_text="一日后",
                timeline_id="main",
                order_basis="relative_to_anchor",
                order_confidence=0.95,
            ),
        ]
    )
    await async_db_session.commit()

    result = await reflow_project_timeline(async_db_session, project_id="proj_a")
    rows = list(
        (
            await async_db_session.execute(
                select(ConsistencyClaim).order_by(ConsistencyClaim.fingerprint)
            )
        )
        .scalars()
        .all()
    )
    assert result.cyclic == 2
    assert result.cycles
    assert all(row.story_order is None for row in rows)
    assert {row.temporal_resolution["dependency_status"] for row in rows} == {"cyclic"}


async def test_project_reflow_ignores_rejected_and_superseded_claims(
    async_db_session, seed_project, make_claim
):
    await seed_project()
    rejected = make_claim(
        project_id="proj_a",
        chapter_id="ch_a",
        fingerprint="rejected",
        status="rejected",
        story_order=99,
    )
    superseded = make_claim(
        project_id="proj_a",
        chapter_id="ch_a",
        fingerprint="superseded",
        status="superseded",
        story_order=101,
    )
    async_db_session.add_all([rejected, superseded])
    await async_db_session.commit()

    result = await reflow_project_timeline(async_db_session, project_id="proj_a")
    assert result.claims_examined == 0
    assert float(rejected.story_order) == 99
    assert float(superseded.story_order) == 101


async def test_temporal_rescans_are_current_revision_only_and_idempotent(
    async_db_session, seed_project, make_run
):
    await seed_project(chapter_ids=("ch_a", "ch_b"))
    async_db_session.add_all(
        [
            ChapterBody(
                chapter_id="ch_a", content_html="<p>A</p>", content_json={}, rev=2
            ),
            ChapterBody(
                chapter_id="ch_b", content_html="<p>B</p>", content_json={}, rev=3
            ),
            make_run(project_id="proj_a", chapter_id="ch_a", body_rev=1),
            make_run(
                project_id="proj_a", chapter_id="ch_a", body_rev=2,
                status="completed", extract_state="succeeded",
                summary_state="succeeded", scan_state="succeeded",
            ),
            make_run(
                project_id="proj_a", chapter_id="ch_b", body_rev=3,
                status="completed", extract_state="succeeded",
                summary_state="succeeded", scan_state="succeeded",
            ),
        ]
    )
    await async_db_session.commit()

    first = await enqueue_temporal_rescans(
        async_db_session,
        project_id="proj_a",
        chapter_ids=["ch_a", "ch_b"],
        cause_id="extract-run-7",
        exclude_chapter_id="ch_a",
    )
    await async_db_session.commit()
    second = await enqueue_temporal_rescans(
        async_db_session,
        project_id="proj_a",
        chapter_ids=["ch_a", "ch_b"],
        cause_id="extract-run-7",
        exclude_chapter_id="ch_a",
    )
    await async_db_session.commit()

    events = list(
        (
            await async_db_session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.topic == "consistency.timeline_rescan"
                )
            )
        )
        .scalars()
        .all()
    )
    assert first == second
    assert len(first) == 1
    assert len(events) == 1
    assert events[0].payload["chapter_id"] == "ch_b"
    assert events[0].payload["body_rev"] == 3
