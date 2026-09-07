import pytest

from services.temporal_decisions import (
    MAX_OVERRIDE_HISTORY,
    TemporalDecisionConflictError,
    TemporalDecisionError,
    decide_temporal_review,
    list_temporal_reviews,
)
from services.temporal_reflow import reflow_project_timeline
from services.timeline import parse_absolute_anchor


async def _seed_reviewable_claims(session, seed_project, make_claim):
    await seed_project(chapter_ids=("ch_a", "ch_b"))
    root = make_claim(
        project_id="proj_a",
        chapter_id="ch_a",
        fingerprint="root",
        timeline_id="main",
        temporal_event_ref="启程",
        temporal_anchor_text="2026-01-01",
        temporal_anchor_value="2026-01-01",
        order_basis="absolute_datetime",
        order_confidence=0.95,
    )
    fuzzy = make_claim(
        project_id="proj_a",
        chapter_id="ch_b",
        fingerprint="fuzzy",
        timeline_id="main",
        temporal_event_ref="开市",
        temporal_relation="after",
        temporal_relation_ref="启程",
        temporal_anchor_text="过几日后",
        order_basis="relative_to_anchor",
        order_confidence=0.95,
    )
    session.add_all([root, fuzzy])
    await session.commit()
    await reflow_project_timeline(session, project_id="proj_a")
    await session.commit()
    return root, fuzzy


async def test_confirm_and_clear_fuzzy_time_preserves_an_audit_history(
    async_db_session, seed_project, make_claim
):
    _, fuzzy = await _seed_reviewable_claims(
        async_db_session, seed_project, make_claim
    )
    reviews = await list_temporal_reviews(async_db_session, project_id="proj_a")
    assert len(reviews) == 1
    assert reviews[0].override_seconds is None
    assert reviews[0].offset_min_seconds == 2 * 86400

    confirmed = await decide_temporal_review(
        async_db_session,
        project_id="proj_a",
        claim_id=fuzzy.id,
        actor_id="user_a",
        action="confirm",
        expected_version=0,
        offset_seconds=4 * 86400,
    )
    await async_db_session.commit()
    await async_db_session.refresh(fuzzy)
    assert confirmed.item.override_seconds == 4 * 86400
    assert confirmed.item.override_version == 1
    assert float(fuzzy.story_order) == parse_absolute_anchor("2026-01-01") + 4 * 86400
    assert fuzzy.temporal_resolution["dependency_status"] == "resolved"
    assert fuzzy.temporal_resolution["author_override_history"][0]["actor_id"] == "user_a"

    cleared = await decide_temporal_review(
        async_db_session,
        project_id="proj_a",
        claim_id=fuzzy.id,
        actor_id="user_a",
        action="clear",
        expected_version=1,
    )
    await async_db_session.commit()
    await async_db_session.refresh(fuzzy)
    assert cleared.item.override_seconds is None
    assert cleared.item.override_version == 2
    assert fuzzy.story_order is None
    assert [entry["action"] for entry in fuzzy.temporal_resolution["author_override_history"]] == [
        "confirm",
        "clear",
    ]


async def test_temporal_decision_rejects_stale_or_out_of_range_input(
    async_db_session, seed_project, make_claim
):
    _, fuzzy = await _seed_reviewable_claims(
        async_db_session, seed_project, make_claim
    )
    claim_id = fuzzy.id
    with pytest.raises(TemporalDecisionError):
        await decide_temporal_review(
            async_db_session,
            project_id="proj_a",
            claim_id=claim_id,
            actor_id="user_a",
            action="confirm",
            expected_version=0,
            offset_seconds=8 * 86400,
        )
    await async_db_session.rollback()

    await decide_temporal_review(
        async_db_session,
        project_id="proj_a",
        claim_id=claim_id,
        actor_id="user_a",
        action="confirm",
        expected_version=0,
        offset_seconds=3 * 86400,
    )
    await async_db_session.commit()
    with pytest.raises(TemporalDecisionConflictError):
        await decide_temporal_review(
            async_db_session,
            project_id="proj_a",
            claim_id=claim_id,
            actor_id="user_a",
            action="clear",
            expected_version=0,
        )


async def test_temporal_decision_bounds_and_sanitizes_embedded_history(
    async_db_session, seed_project, make_claim
):
    _, fuzzy = await _seed_reviewable_claims(async_db_session, seed_project, make_claim)
    fuzzy.temporal_resolution = {
        **(fuzzy.temporal_resolution or {}),
        "author_override_version": MAX_OVERRIDE_HISTORY,
        "author_override_history": [
            {"version": version, "action": "confirm"}
            for version in range(1, MAX_OVERRIDE_HISTORY + 1)
        ] + ["invalid"],
    }
    await async_db_session.commit()

    await decide_temporal_review(
        async_db_session,
        project_id="proj_a",
        claim_id=fuzzy.id,
        actor_id="user_a",
        action="confirm",
        expected_version=MAX_OVERRIDE_HISTORY,
        offset_seconds=4 * 86400,
    )
    await async_db_session.commit()
    await async_db_session.refresh(fuzzy)

    history = fuzzy.temporal_resolution["author_override_history"]
    assert len(history) == MAX_OVERRIDE_HISTORY
    assert history[0]["version"] == 2
    assert history[-1]["version"] == MAX_OVERRIDE_HISTORY + 1
