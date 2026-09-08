from unittest.mock import AsyncMock

import pytest

from db.models_consistency import ChapterOutlineState
from db.models_codex import CodexEntry
from db.models_core import Chapter, ChapterBody
from domain.outlines import BodyPolicy
from services.scene_cards import (
    SceneBodyPolicyRequiredError,
    ScenePlanRevisionConflictError,
    SceneReferenceError,
    SceneRevisionConflictError,
    archive_scene,
    create_scene,
    list_scenes,
    reorder_scenes,
    update_scene,
)


@pytest.mark.asyncio
async def test_scene_card_create_is_additive_and_stable(seed_project, async_db_session, monkeypatch):
    await seed_project(chapter_ids=("ch-scenes",))
    monkeypatch.setattr("services.scene_cards.OutboxService.enqueue", AsyncMock())

    scene = await create_scene(
        async_db_session,
        chapter_id="ch-scenes",
        order=1,
        fields={"goal": "找到失踪的妹妹", "hook": "门后传来第二个脚步声"},
        base_outline_rev=0,
        base_body_rev=0,
        body_policy=None,
    )
    await async_db_session.commit()

    assert scene.id.startswith("sc_")
    assert scene.rev == 1
    assert scene.outline_rev == 0
    assert scene.body_rev is None
    assert scene.goal == "找到失踪的妹妹"
    chapter = await async_db_session.get(Chapter, "ch-scenes")
    assert chapter.outline == []
    assert [item.id for item in await list_scenes(async_db_session, "ch-scenes")] == [scene.id]


@pytest.mark.asyncio
async def test_scene_card_requires_body_policy_and_can_mark_body(seed_project, async_db_session, monkeypatch):
    await seed_project(chapter_ids=("ch-body",))
    async_db_session.add(
        ChapterBody(
            chapter_id="ch-body",
            content_html="<p>正文</p>",
            content_json={"type": "doc"},
            rev=4,
        )
    )
    await async_db_session.flush()
    monkeypatch.setattr("services.scene_cards.OutboxService.enqueue", AsyncMock())

    with pytest.raises(SceneBodyPolicyRequiredError):
        await create_scene(
            async_db_session,
            chapter_id="ch-body",
            order=1,
            fields={"goal": "目标"},
            base_outline_rev=0,
            base_body_rev=4,
            body_policy=None,
        )

    scene = await create_scene(
        async_db_session,
        chapter_id="ch-body",
        order=1,
        fields={"goal": "目标"},
        base_outline_rev=0,
        base_body_rev=4,
        body_policy=BodyPolicy.MARK_BODY_FOR_REVISION,
    )
    await async_db_session.flush()
    state = await async_db_session.get(ChapterOutlineState, "ch-body")
    assert scene.body_rev == 4
    assert state is not None and state.body_needs_revision is True
    assert state.marked_body_rev == 4


@pytest.mark.asyncio
async def test_scene_card_revisions_and_stale_plan_are_rejected(seed_project, async_db_session, monkeypatch):
    await seed_project(chapter_ids=("ch-revisions",))
    monkeypatch.setattr("services.scene_cards.OutboxService.enqueue", AsyncMock())
    scene = await create_scene(
        async_db_session,
        chapter_id="ch-revisions",
        order=1,
        fields={"goal": "旧目标"},
        base_outline_rev=0,
        base_body_rev=0,
        body_policy=None,
    )
    await async_db_session.flush()

    with pytest.raises(SceneRevisionConflictError):
        await update_scene(
            async_db_session,
            scene_id_value=scene.id,
            expected_rev=99,
            fields={"goal": "覆盖"},
            base_outline_rev=0,
            base_body_rev=0,
            body_policy=None,
        )
    state = ChapterOutlineState(chapter_id="ch-revisions", revision=2)
    async_db_session.add(state)
    await async_db_session.flush()
    with pytest.raises(ScenePlanRevisionConflictError):
        await update_scene(
            async_db_session,
            scene_id_value=scene.id,
            expected_rev=1,
            fields={"goal": "旧规划覆盖"},
            base_outline_rev=0,
            base_body_rev=0,
            body_policy=None,
        )


@pytest.mark.asyncio
async def test_scene_reorder_and_archive(seed_project, async_db_session, monkeypatch):
    await seed_project(chapter_ids=("ch-order",))
    monkeypatch.setattr("services.scene_cards.OutboxService.enqueue", AsyncMock())
    first = await create_scene(
        async_db_session,
        chapter_id="ch-order",
        order=1,
        fields={"goal": "一"},
        base_outline_rev=0,
        base_body_rev=0,
        body_policy=None,
    )
    second = await create_scene(
        async_db_session,
        chapter_id="ch-order",
        order=2,
        fields={"goal": "二"},
        base_outline_rev=0,
        base_body_rev=0,
        body_policy=None,
    )
    await async_db_session.flush()
    ordered = await reorder_scenes(
        async_db_session,
        chapter_id="ch-order",
        scene_ids=[second.id, first.id],
        base_outline_rev=0,
        base_body_rev=0,
        body_policy=None,
    )
    assert [item.id for item in ordered] == [second.id, first.id]
    assert [item.order for item in ordered] == [1, 2]
    archived = await archive_scene(
        async_db_session,
        scene_id_value=first.id,
        expected_rev=2,
        base_outline_rev=0,
        base_body_rev=0,
        body_policy=None,
    )
    await async_db_session.flush()
    assert archived.status == "archived"
    assert [item.id for item in await list_scenes(async_db_session, "ch-order")] == [second.id]


@pytest.mark.asyncio
async def test_scene_rejects_wrong_codex_kind(seed_project, async_db_session, monkeypatch):
    await seed_project(chapter_ids=("ch-reference",))
    async_db_session.add(
        CodexEntry(
            id="entry-place",
            project_id="proj_a",
            kind="location",
            name="旧宅",
            description="城南旧宅",
            attrs={},
            resident=False,
            status="confirmed",
            ref_chapters=[],
            conflicts=[],
        )
    )
    await async_db_session.flush()
    monkeypatch.setattr("services.scene_cards.OutboxService.enqueue", AsyncMock())

    with pytest.raises(SceneReferenceError):
        await create_scene(
            async_db_session,
            chapter_id="ch-reference",
            order=1,
            fields={"pov_entry_id": "entry-place"},
            base_outline_rev=0,
            base_body_rev=0,
            body_policy=None,
        )
