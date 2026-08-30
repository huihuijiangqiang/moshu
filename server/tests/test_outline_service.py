from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from db.models_consistency import ChapterOutlineRevision, ChapterOutlineState
from domain.outlines import BodyPolicy, BodyPolicyRequiredError
from services.outlines import OutlineRevisionConflictError, acknowledge_body_revision, update_outline


class ScalarResult:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


def fake_db(*results):
    return SimpleNamespace(
        execute=AsyncMock(side_effect=[ScalarResult(result) for result in results]),
        add=Mock(),
        flush=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_update_outline_marks_body_without_modifying_it(monkeypatch) -> None:
    chapter = SimpleNamespace(id="ch-1", project_id="p-1", title="旧章", outline=["旧节点"])
    state = ChapterOutlineState(
        chapter_id="ch-1",
        revision=2,
        note="旧注记",
        body_needs_revision=False,
        marked_outline_rev=None,
        marked_body_rev=None,
    )
    body = SimpleNamespace(rev=7, content_html="<p>正文不能改</p>", content_json={"type": "doc"})
    body_before = deepcopy(vars(body))
    db = fake_db(chapter, state, body)
    enqueue = AsyncMock()
    monkeypatch.setattr("services.outlines.OutboxService.enqueue", enqueue)

    result = await update_outline(
        db,
        chapter_id="ch-1",
        title="新章",
        nodes=[" 新节点 ", ""],
        note=" 新注记 ",
        base_outline_revision=2,
        body_policy=BodyPolicy.MARK_BODY_FOR_REVISION,
    )

    assert vars(body) == body_before
    assert chapter.title == "新章"
    assert chapter.outline == ["新节点"]
    assert result.state.body_needs_revision is True
    assert result.state.marked_outline_rev == 3
    assert result.state.marked_body_rev == 7
    snapshots = [call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], ChapterOutlineRevision)]
    assert len(snapshots) == 1
    assert snapshots[0].body_policy == "mark_body_for_revision"
    enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_outline_requires_policy_when_body_exists(monkeypatch) -> None:
    chapter = SimpleNamespace(id="ch-1", project_id="p-1", title="旧章", outline=[])
    state = ChapterOutlineState(
        chapter_id="ch-1",
        revision=1,
        note="",
        body_needs_revision=False,
        marked_outline_rev=None,
        marked_body_rev=None,
    )
    body = SimpleNamespace(rev=4, content_html="正文")
    db = fake_db(chapter, state, body)
    monkeypatch.setattr("services.outlines.OutboxService.enqueue", AsyncMock())

    with pytest.raises(BodyPolicyRequiredError):
        await update_outline(
            db,
            chapter_id="ch-1",
            title="新章",
            nodes=[],
            note="",
            base_outline_revision=1,
            body_policy=None,
        )

    assert body.content_html == "正文"
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_revision_conflict_returns_current_plan_without_reading_body(monkeypatch) -> None:
    chapter = SimpleNamespace(id="ch-1", project_id="p-1", title="服务端章名", outline=["服务端节点"])
    state = ChapterOutlineState(
        chapter_id="ch-1",
        revision=5,
        note="服务端注记",
        body_needs_revision=False,
        marked_outline_rev=None,
        marked_body_rev=None,
    )
    db = fake_db(chapter, state)
    monkeypatch.setattr("services.outlines.OutboxService.enqueue", AsyncMock())

    with pytest.raises(OutlineRevisionConflictError) as exc_info:
        await update_outline(
            db,
            chapter_id="ch-1",
            title="客户端章名",
            nodes=[],
            note="",
            base_outline_revision=4,
            body_policy=BodyPolicy.PLAN_ONLY,
        )

    assert exc_info.value.actual == 5
    assert exc_info.value.current.title == "服务端章名"
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_noop_does_not_create_snapshot_or_outbox(monkeypatch) -> None:
    chapter = SimpleNamespace(id="ch-1", project_id="p-1", title="章名", outline=["节点"])
    state = ChapterOutlineState(
        chapter_id="ch-1",
        revision=3,
        note="注记",
        body_needs_revision=True,
        marked_outline_rev=2,
        marked_body_rev=6,
    )
    db = fake_db(chapter, state)
    enqueue = AsyncMock()
    monkeypatch.setattr("services.outlines.OutboxService.enqueue", enqueue)

    result = await update_outline(
        db,
        chapter_id="ch-1",
        title=" 章名 ",
        nodes=[" 节点 "],
        note=" 注记 ",
        base_outline_revision=3,
        body_policy=None,
    )

    assert result.state.revision == 3
    db.add.assert_not_called()
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_acknowledge_marker_only_clears_state(monkeypatch) -> None:
    chapter = SimpleNamespace(id="ch-1", project_id="p-1", title="章名", outline=[])
    state = ChapterOutlineState(
        chapter_id="ch-1",
        revision=5,
        note="",
        body_needs_revision=True,
        marked_outline_rev=3,
        marked_body_rev=7,
    )
    body = SimpleNamespace(rev=9, content_html="<p>保持原文</p>", content_json={"type": "doc"})
    body_before = deepcopy(vars(body))
    db = fake_db(chapter, state, body)
    monkeypatch.setattr("services.outlines.OutboxService.enqueue", AsyncMock())

    result = await acknowledge_body_revision(
        db,
        chapter_id="ch-1",
        base_body_rev=9,
        addressed_outline_revision=5,
    )

    assert vars(body) == body_before
    assert result.state.body_needs_revision is False
    assert state.marked_outline_rev is None
    assert state.marked_body_rev is None
