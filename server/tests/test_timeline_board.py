from services.timeline import parse_absolute_anchor
from services.timeline_board import build_timeline_board


async def test_timeline_board_groups_lanes_and_keeps_unresolved_events_visible(
    async_db_session, seed_project, make_claim
):
    await seed_project(chapter_ids=("ch_a", "ch_b", "ch_c"))
    root_order = parse_absolute_anchor("2026-01-01")
    async_db_session.add_all(
        [
            make_claim(
                project_id="proj_a",
                chapter_id="ch_a",
                fingerprint="timeline-main-root",
                timeline_id="main",
                temporal_event_ref="启程",
                temporal_anchor_text="2026-01-01",
                temporal_anchor_value="2026-01-01",
                order_basis="absolute_datetime",
                story_order=root_order,
                order_confidence=0.95,
            ),
            make_claim(
                project_id="proj_a",
                chapter_id="ch_b",
                fingerprint="timeline-main-review",
                timeline_id="main",
                temporal_event_ref="开市",
                temporal_anchor_text="过几日后",
                temporal_relation="after",
                temporal_relation_ref="启程",
                temporal_resolution={"dependency_status": "unresolved"},
                order_basis="relative_to_anchor",
                order_confidence=0.9,
            ),
            make_claim(
                project_id="proj_a",
                chapter_id="ch_c",
                fingerprint="timeline-side",
                timeline_id="沈禾支线",
                temporal_event_ref="发现水渠",
                temporal_anchor_text="2025-12-30",
                temporal_anchor_value="2025-12-30",
                order_basis="absolute_datetime",
                story_order=parse_absolute_anchor("2025-12-30"),
                order_confidence=0.91,
            ),
        ]
    )
    await async_db_session.commit()

    board = await build_timeline_board(async_db_session, project_id="proj_a")

    assert board.event_count == 3
    assert board.placed_count == 2
    assert board.review_count == 1
    assert [lane.timeline_id for lane in board.lanes] == ["main", "沈禾支线"]
    assert board.lanes[0].label == "主线"
    assert board.lanes[0].events[1].placement_status == "review"
    assert board.lanes[0].events[1].relation_ref == "启程"


async def test_timeline_board_marks_author_resolution_and_unassigned_lane(
    async_db_session, seed_project, make_claim
):
    await seed_project(chapter_ids=("ch_a",))
    async_db_session.add(
        make_claim(
            project_id="proj_a",
            chapter_id="ch_a",
            fingerprint="timeline-unassigned",
            timeline_id=None,
            temporal_event_ref="入夜",
            temporal_anchor_text="傍晚",
            temporal_resolution={"dependency_status": "ambiguous"},
            order_basis="relative_to_anchor",
            order_confidence=0.7,
        )
    )
    await async_db_session.commit()

    board = await build_timeline_board(async_db_session, project_id="proj_a")

    assert board.lanes[0].timeline_id == "unassigned"
    assert board.lanes[0].label == "未归线"
    assert board.lanes[0].events[0].placement_status == "ambiguous"
