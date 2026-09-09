from datetime import UTC, datetime, timedelta

import pytest

from db.models_long_generation import GenerationSegment
from services.long_generation_executor import (
    SegmentBusyError,
    SegmentCheckpointConflictError,
    SegmentLeaseLostError,
    SegmentMergeBlockedError,
    accept_ready_segment,
    checkpoint_hash,
    checkpoint_segment,
    claim_segment,
    fail_segment,
    heartbeat_segment,
    merge_segment_outputs,
    validate_claimed_segment,
)


async def _segment(async_db_session, *, index=0, status="pending", content="", updated_at=None):
    row = GenerationSegment(
        id=f"segment_{index}",
        project_id="executor_project",
        chapter_id="executor_chapter",
        segment_index=index,
        target_words=800,
        status=status,
        content_text=content,
        generated_words=0,
        revision=1,
        context_manifest={"purpose": f"scene {index}"},
        updated_at=updated_at or datetime.now(UTC),
    )
    async_db_session.add(row)
    await async_db_session.flush()
    return row


@pytest.fixture
async def executor_scope(async_db_session, seed_project):
    await seed_project(
        user_id="executor_writer",
        project_id="executor_project",
        chapter_ids=("executor_chapter",),
    )


async def test_claim_checkpoint_failure_and_retry_are_fenced(async_db_session, executor_scope):
    row = await _segment(async_db_session)
    now = datetime.now(UTC)

    first = await claim_segment(async_db_session, row.id, now=now)
    assert first.lease_revision == 2
    assert first.resumed is False
    assert first.lease_owner is None
    assert first.lease_expires_at > now
    with pytest.raises(SegmentBusyError):
        await claim_segment(async_db_session, row.id, now=now + timedelta(seconds=10))

    saved = await checkpoint_segment(
        async_db_session,
        row.id,
        lease_revision=first.lease_revision,
        expected_checkpoint_hash=first.checkpoint_hash,
        content_text="第一批正文已经落盘。" * 80,
        now=now + timedelta(seconds=20),
    )
    assert saved.generated_words > 0
    with pytest.raises(SegmentCheckpointConflictError):
        await checkpoint_segment(
            async_db_session,
            row.id,
            lease_revision=first.lease_revision,
            expected_checkpoint_hash=first.checkpoint_hash,
            content_text="过期写入",
        )

    await fail_segment(
        async_db_session,
        row.id,
        lease_revision=first.lease_revision,
        error_code="provider_timeout",
    )
    retry = await claim_segment(async_db_session, row.id)
    assert retry.lease_revision == 3
    assert retry.resumed is True
    assert retry.content_text.startswith("第一批正文")
    with pytest.raises(SegmentLeaseLostError):
        await checkpoint_segment(
            async_db_session,
            row.id,
            lease_revision=first.lease_revision,
            expected_checkpoint_hash=saved.checkpoint_hash,
            content_text=retry.content_text + "旧 worker 迟到。",
        )


async def test_heartbeat_extends_lease_and_wrong_owner_is_fenced(async_db_session, executor_scope):
    row = await _segment(async_db_session)
    now = datetime.now(UTC)
    claim = await claim_segment(async_db_session, row.id, lease_owner="worker-a", now=now, lease_seconds=30)
    beat = await heartbeat_segment(
        async_db_session,
        row.id,
        lease_revision=claim.lease_revision,
        lease_owner="worker-a",
        lease_seconds=120,
        now=now + timedelta(seconds=10),
    )
    assert beat.lease_owner == "worker-a"
    assert beat.lease_expires_at == now + timedelta(seconds=130)
    with pytest.raises(SegmentLeaseLostError):
        await heartbeat_segment(
            async_db_session,
            row.id,
            lease_revision=claim.lease_revision,
            lease_owner="worker-b",
            now=now + timedelta(seconds=20),
        )


async def test_expired_running_segment_can_be_reclaimed(async_db_session, executor_scope):
    now = datetime.now(UTC)
    row = await _segment(
        async_db_session,
        status="running",
        content="已保存片段。",
        updated_at=now - timedelta(minutes=10),
    )

    claim = await claim_segment(async_db_session, row.id, now=now, lease_seconds=300)
    assert claim.lease_revision == 2
    assert claim.resumed is True


async def test_legacy_running_segment_ignores_requesters_short_lease(
    async_db_session,
    executor_scope,
):
    now = datetime.now(UTC)
    row = await _segment(
        async_db_session,
        status="running",
        updated_at=now - timedelta(seconds=10),
    )

    with pytest.raises(SegmentBusyError):
        await claim_segment(
            async_db_session,
            row.id,
            now=now,
            lease_seconds=1,
        )

    reclaimed = await claim_segment(
        async_db_session,
        row.id,
        now=now + timedelta(seconds=301),
        lease_seconds=1,
    )
    assert reclaimed.lease_revision == 2


async def test_validation_persists_evidence_and_allows_retry(async_db_session, executor_scope):
    row = await _segment(async_db_session)
    claim = await claim_segment(async_db_session, row.id)
    short_text = "粮账出现了。"
    saved = await checkpoint_segment(
        async_db_session,
        row.id,
        lease_revision=claim.lease_revision,
        expected_checkpoint_hash=claim.checkpoint_hash,
        content_text=short_text,
    )

    check = await validate_claimed_segment(
        async_db_session,
        row.id,
        lease_revision=claim.lease_revision,
        required_terms=["粮账"],
    )
    assert check.blocking is True
    assert row.status == "failed"
    assert row.error_code == "validation_failed"
    assert row.lease_owner is None
    assert row.lease_expires_at is None
    assert row.context_manifest["lastValidation"]["checkpointHash"] == saved.checkpoint_hash

    retry = await claim_segment(async_db_session, row.id)
    full_text = short_text + ("沈禾核对粮账后，当众指出账目漏洞并逼周掌柜重新报价。" * 40)
    await checkpoint_segment(
        async_db_session,
        row.id,
        lease_revision=retry.lease_revision,
        expected_checkpoint_hash=retry.checkpoint_hash,
        content_text=full_text,
    )
    passed = await validate_claimed_segment(
        async_db_session,
        row.id,
        lease_revision=retry.lease_revision,
        required_terms=["粮账"],
    )
    assert passed.blocking is False
    assert row.status == "ready"
    assert row.completed_at is not None
    assert row.context_manifest["lastValidation"]["status"] == "ready"


async def test_merge_requires_contiguous_ready_segments_and_deduplicates_boundary(async_db_session, executor_scope):
    boundary = "这是需要跨段保留且不能重复出现的边界句子。"
    first = await _segment(async_db_session, index=0, status="ready", content="第一段正文。" + boundary)
    second = await _segment(async_db_session, index=1, status="ready", content=boundary + "第二段正文。")
    skipped = await _segment(async_db_session, index=2, status="skipped")

    merged = merge_segment_outputs([second, skipped, first])
    assert merged.segment_ids == (first.id, second.id)
    assert merged.content_text.count(boundary) == 1
    assert merged.content_hash == checkpoint_hash(merged.content_text)

    second.status = "failed"
    with pytest.raises(SegmentMergeBlockedError):
        merge_segment_outputs([first, second])


async def test_checkpoint_cannot_truncate_or_replace_saved_prose(async_db_session, executor_scope):
    row = await _segment(async_db_session, content="不可丢失的开头。")
    claim = await claim_segment(async_db_session, row.id)

    with pytest.raises(SegmentCheckpointConflictError):
        await checkpoint_segment(
            async_db_session,
            row.id,
            lease_revision=claim.lease_revision,
            expected_checkpoint_hash=claim.checkpoint_hash,
            content_text="另一个开头。",
        )


async def test_accept_ready_segment_is_fenced_and_idempotent(async_db_session, executor_scope):
    row = await _segment(
        async_db_session,
        status="ready",
        content="沈砚秋把复核后的粮账交给在场三人逐一按印。",
    )

    accepted = await accept_ready_segment(
        async_db_session,
        row.id,
        lease_revision=row.revision,
    )
    assert accepted.idempotent is False
    assert row.status == "accepted"

    repeated = await accept_ready_segment(
        async_db_session,
        row.id,
        lease_revision=row.revision,
    )
    assert repeated.idempotent is True
    assert repeated.content_hash == accepted.content_hash

    with pytest.raises(SegmentLeaseLostError):
        await accept_ready_segment(
            async_db_session,
            row.id,
            lease_revision=row.revision - 1,
        )
