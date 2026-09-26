from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from db.models_long_generation import GenerationSegment
from db.models_usage import GenerationRun
from services.generation import count_generated_words
from services.long_generation_runner import LongSegmentOptions, _append_stream, execute_long_segment
from services.usage import UsageReservation


def test_append_stream_preserves_checkpoint_prefix_and_spacing():
    existing = "已经保存的段落结尾。\n"

    continued = _append_stream(existing, "接着推进新的动作。")

    assert continued.startswith(existing)
    assert continued == "已经保存的段落结尾。\n接着推进新的动作。"


def test_append_stream_preserves_intentional_repetition():
    existing = "已经保存的段落结尾，门外的脚步声越来越近，所有人都屏住了呼吸。"

    continued = _append_stream(existing, existing + "新的动作。")

    assert continued.startswith(existing)
    assert continued == existing + existing + "新的动作。"


@pytest.mark.parametrize("chunk_size", [1, 2, 7, 100])
def test_stream_boundaries_do_not_change_prose(chunk_size):
    prose = "林照推开舱门。\n\n  ‘Not yet,’ she said.\n引航者-7亮起了灯。\n"
    content = "断点前的正文。"
    for offset in range(0, len(prose), chunk_size):
        content = _append_stream(content, prose[offset:offset + chunk_size])
    assert content == "断点前的正文。" + prose


@pytest.mark.parametrize("chunk_size", [1, 17, 4096])
async def test_execute_long_segment_checkpoints_validates_and_records_run(
    async_db_session, seed_project, monkeypatch, chunk_size
):
    await seed_project(
        user_id="runner_writer",
        project_id="runner_project",
        chapter_ids=("runner_chapter",),
    )
    segment = GenerationSegment(
        id="runner_segment",
        project_id="runner_project",
        chapter_id="runner_chapter",
        segment_index=0,
        target_words=800,
        status="running",
        revision=1,
        lease_owner="runner-worker",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=20),
        heartbeat_at=datetime.now(UTC),
        context_manifest={"purpose": "核对粮账并留下新的风险"},
    )
    async_db_session.add(segment)
    await async_db_session.commit()

    content = "沈禾核对粮账并记录新的证据，门外忽然传来急促的脚步声。\n\n" * 35
    package = SimpleNamespace(
        route=SimpleNamespace(source="platform", config_id=None),
        model_id="gpt-test",
        model_tier="main",
        prompt_tokens=1200,
        messages=[{"role": "system", "content": "test"}],
        layer_report={"context": {"used_tokens": 1200}},
        preflight={"blocking": False},
    )

    class FakeService:
        def __init__(self, _db):
            pass

        async def prepare(self, **_kwargs):
            return package

    async def fake_stream(_gateway, _package):
        for offset in range(0, len(content), chunk_size):
            yield SimpleNamespace(type="chunk", text=content[offset:offset + chunk_size], usage=None)
        yield SimpleNamespace(type="usage", text="", usage={"prompt_tokens": 1200, "completion_tokens": 900})

    async def fake_reserve(_db, **_kwargs):
        return UsageReservation(log_id=1, reserved_credits=20)

    async def fake_settle(_db, _reservation, **_kwargs):
        return 18

    monkeypatch.setattr("services.long_generation_runner.GenerationService", FakeService)
    monkeypatch.setattr("services.long_generation_runner.CHECKPOINT_CHAR_INTERVAL", 200)
    monkeypatch.setattr("services.long_generation_runner.retryable_stream", fake_stream)
    monkeypatch.setattr("services.long_generation_runner.reserve_generation", fake_reserve)
    monkeypatch.setattr("services.long_generation_runner.settle_generation", fake_settle)

    result = await execute_long_segment(
        async_db_session,
        segment_id=segment.id,
        user_id="runner_writer",
        lease_revision=1,
        lease_owner="runner-worker",
        options=LongSegmentOptions(context_mode="deep"),
    )

    assert result["status"] == "ready"
    assert result["generatedWords"] >= 800
    assert result["credits"] == 18
    row = await async_db_session.get(GenerationSegment, segment.id)
    assert row is not None and row.status == "ready"
    assert row.content_text == content
    assert row.run_id == result["runId"]
    assert row.generated_words >= 800
    assert row.context_manifest["generationRequest"]["contextMode"] == "deep"
    run = await async_db_session.get(GenerationRun, result["runId"])
    assert run is not None and run.task_type == "long_segment"
    assert (await async_db_session.execute(select(GenerationRun))).scalars().all()


async def test_execute_long_segment_requests_only_remaining_words_after_checkpoint(
    async_db_session, seed_project, monkeypatch
):
    await seed_project(
        user_id="resume_writer",
        project_id="resume_project",
        chapter_ids=("resume_chapter",),
    )
    prefix = "已保存的正文前缀记录粮账和脚步声。" * 20
    continuation = "她顺着脚步声追到库门，新的证据让局面再次改变。" * 35
    segment = GenerationSegment(
        id="resume_segment",
        project_id="resume_project",
        chapter_id="resume_chapter",
        segment_index=0,
        target_words=800,
        status="running",
        revision=1,
        content_text=prefix,
        lease_owner="resume-worker",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=20),
        heartbeat_at=datetime.now(UTC),
        context_manifest={"purpose": "从已保存正文继续"},
    )
    async_db_session.add(segment)
    await async_db_session.commit()

    package = SimpleNamespace(
        route=SimpleNamespace(source="platform", config_id=None),
        model_id="gpt-test",
        model_tier="main",
        prompt_tokens=900,
        messages=[{"role": "system", "content": "test"}],
        layer_report={"context": {"used_tokens": 900}},
        preflight={"blocking": False},
    )
    prepared_targets: list[int] = []
    reserved_targets: list[int] = []

    class FakeService:
        def __init__(self, _db):
            pass

        async def prepare(self, **kwargs):
            prepared_targets.append(kwargs["target_words"])
            return package

    async def fake_stream(_gateway, _package):
        yield SimpleNamespace(type="chunk", text=continuation, usage=None)
        yield SimpleNamespace(type="usage", text="", usage={"prompt_tokens": 900, "completion_tokens": 500})

    async def fake_reserve(_db, **kwargs):
        reserved_targets.append(kwargs["target_words"])
        return UsageReservation(log_id=2, reserved_credits=20)

    async def fake_settle(_db, _reservation, **_kwargs):
        return 18

    monkeypatch.setattr("services.long_generation_runner.GenerationService", FakeService)
    monkeypatch.setattr("services.long_generation_runner.retryable_stream", fake_stream)
    monkeypatch.setattr("services.long_generation_runner.reserve_generation", fake_reserve)
    monkeypatch.setattr("services.long_generation_runner.settle_generation", fake_settle)

    result = await execute_long_segment(
        async_db_session,
        segment_id=segment.id,
        user_id="resume_writer",
        lease_revision=1,
        lease_owner="resume-worker",
        options=LongSegmentOptions(),
    )

    remaining = max(100, segment.target_words - count_generated_words(prefix))
    assert result["status"] == "ready"
    assert prepared_targets == [remaining]
    assert reserved_targets == [remaining]
    row = await async_db_session.get(GenerationSegment, segment.id)
    assert row.content_text == prefix + continuation


async def test_execute_long_segment_accepts_a_complete_checkpoint_without_regenerating(
    async_db_session, seed_project, monkeypatch
):
    await seed_project(
        user_id="checkpoint_writer",
        project_id="checkpoint_project",
        chapter_ids=("checkpoint_chapter",),
    )
    content = "沈禾核对粮账，门外传来脚步声，所有人都停下手里的动作。" * 40
    segment = GenerationSegment(
        id="checkpoint_segment",
        project_id="checkpoint_project",
        chapter_id="checkpoint_chapter",
        segment_index=0,
        target_words=800,
        status="running",
        revision=2,
        content_text=content,
        generated_words=0,
        lease_owner="checkpoint-worker",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=20),
        heartbeat_at=datetime.now(UTC),
        context_manifest={"purpose": "保留已经生成的正文"},
    )
    async_db_session.add(segment)
    await async_db_session.commit()

    class UnexpectedGeneration:
        def __init__(self, _db):
            raise AssertionError("a complete checkpoint must not call the model")

    monkeypatch.setattr("services.long_generation_runner.GenerationService", UnexpectedGeneration)

    result = await execute_long_segment(
        async_db_session,
        segment_id=segment.id,
        user_id="checkpoint_writer",
        lease_revision=2,
        lease_owner="checkpoint-worker",
        options=LongSegmentOptions(),
    )

    assert result["status"] == "ready"
    assert result["credits"] == 0
    assert result["resumedWithoutGeneration"] is True
    row = await async_db_session.get(GenerationSegment, segment.id)
    assert row is not None and row.status == "ready"
    assert row.generated_words == result["generatedWords"]
