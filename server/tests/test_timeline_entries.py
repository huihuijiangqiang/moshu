from datetime import datetime, timedelta, timezone

import pytest

from services.timeline_board import build_timeline_board
from services.timeline_entries import (
    TimelineEntryConflictError,
    TimelineEntryError,
    archive_timeline_entry,
    create_timeline_entry,
    update_timeline_entry,
)


async def test_author_entry_uses_calendar_time_and_appears_on_board(
    async_db_session, seed_project
):
    await seed_project(chapter_ids=("ch_a",))
    start = datetime(2026, 1, 3, 8, 30, tzinfo=timezone.utc)
    entry = await create_timeline_entry(
        async_db_session,
        project_id="proj_a",
        actor_id="user_a",
        chapter_id="ch_a",
        title="冬集开市",
        detail="女主第一次公开摆摊。",
        timeline_id="main",
        time_text="腊月初八清晨",
        story_order=5,
        time_start=start,
        time_end=start + timedelta(hours=4),
    )
    await async_db_session.commit()

    board = await build_timeline_board(async_db_session, project_id="proj_a")

    assert float(entry.story_order) == start.timestamp()
    assert board.event_count == 1
    event = board.lanes[0].events[0]
    assert event.source == "planned"
    assert event.entry_id == entry.id
    assert event.chapter_id == "ch_a"
    assert event.editable is True
    assert event.revision == 1
    assert event.placement_status == "placed"


async def test_author_entry_update_is_locked_and_archive_removes_projection(
    async_db_session, seed_project
):
    await seed_project(chapter_ids=("ch_a", "ch_b"))
    entry = await create_timeline_entry(
        async_db_session,
        project_id="proj_a",
        actor_id="user_a",
        chapter_id=None,
        title="发现荒田",
        detail=None,
        timeline_id="main",
        time_text="入冬前",
        story_order=None,
        time_start=None,
        time_end=None,
    )
    await async_db_session.commit()
    entry_id = entry.id

    updated = await update_timeline_entry(
        async_db_session,
        project_id="proj_a",
        entry_id=entry_id,
        expected_rev=1,
        chapter_id="ch_b",
        title="发现废弃水渠",
        detail="支线转折",
        timeline_id="水渠支线",
        time_text="入冬前一日",
        story_order=12.5,
        time_start=None,
        time_end=None,
    )
    await async_db_session.commit()
    assert updated.rev == 2
    assert float(updated.story_order) == 12.5

    with pytest.raises(TimelineEntryConflictError) as conflict:
        await archive_timeline_entry(
            async_db_session,
            project_id="proj_a",
            entry_id=entry_id,
            expected_rev=1,
        )
    assert conflict.value.current_rev == 2
    await async_db_session.rollback()

    await archive_timeline_entry(
        async_db_session,
        project_id="proj_a",
        entry_id=entry_id,
        expected_rev=2,
    )
    await async_db_session.commit()
    board = await build_timeline_board(async_db_session, project_id="proj_a")
    assert board.event_count == 0


async def test_author_entry_rejects_cross_project_chapter_and_invalid_range(
    async_db_session, seed_project
):
    await seed_project(project_id="proj_a", chapter_ids=("ch_a",))
    await seed_project(user_id="user_b", project_id="proj_b", chapter_ids=("ch_b",))

    with pytest.raises(TimelineEntryError, match="does not belong"):
        await create_timeline_entry(
            async_db_session,
            project_id="proj_a",
            actor_id="user_a",
            chapter_id="ch_b",
            title="越界事件",
            detail=None,
            timeline_id="main",
            time_text=None,
            story_order=None,
            time_start=None,
            time_end=None,
        )

    start = datetime(2026, 1, 2, tzinfo=timezone.utc)
    with pytest.raises(TimelineEntryError, match="earlier"):
        await create_timeline_entry(
            async_db_session,
            project_id="proj_a",
            actor_id="user_a",
            chapter_id=None,
            title="倒置区间",
            detail=None,
            timeline_id="main",
            time_text=None,
            story_order=None,
            time_start=start,
            time_end=start - timedelta(days=1),
        )
