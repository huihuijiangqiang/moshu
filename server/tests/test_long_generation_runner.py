from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select

from db.models_long_generation import GenerationSegment
from db.models_usage import GenerationRun
from services.long_generation_runner import LongSegmentOptions, execute_long_segment
from services.usage import UsageReservation


async def test_execute_long_segment_checkpoints_validates_and_records_run(
    async_db_session, seed_project, monkeypatch
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

    content = "沈禾核对粮账并记录新的证据，门外忽然传来急促的脚步声。" * 35
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
        yield SimpleNamespace(type="chunk", text=content, usage=None)
        yield SimpleNamespace(type="usage", text="", usage={"prompt_tokens": 1200, "completion_tokens": 900})

    async def fake_reserve(_db, **_kwargs):
        return UsageReservation(log_id=1, reserved_credits=20)

    async def fake_settle(_db, _reservation, **_kwargs):
        return 18

    monkeypatch.setattr("services.long_generation_runner.GenerationService", FakeService)
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
    assert row.run_id == result["runId"]
    assert row.generated_words >= 800
    assert row.context_manifest["generationRequest"]["contextMode"] == "deep"
    run = await async_db_session.get(GenerationRun, result["runId"])
    assert run is not None and run.task_type == "long_segment"
    assert (await async_db_session.execute(select(GenerationRun))).scalars().all()
