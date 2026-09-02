"""Statement and behavior tests for the transactional outbox."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from services.outbox import OutboxPayloadConflictError, OutboxService


class ScalarResult:
    def __init__(self, value=None, values=None, rowcount=0):
        self.value = value
        self.values = values or []
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.values


def compiled_sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect())).lower()


@pytest.mark.asyncio
async def test_enqueue_uses_on_conflict_and_replays_same_payload() -> None:
    existing = SimpleNamespace(payload={"chapter_id": "ch-1"})
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[ScalarResult(), ScalarResult(existing)]),
        flush=AsyncMock(),
    )

    result = await OutboxService.enqueue(db, "chapter.saved", "ch-1", 4, {"chapter_id": "ch-1"})

    assert result is existing
    assert "on conflict" in compiled_sql(db.execute.await_args_list[0].args[0])
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_enqueue_rejects_same_key_with_different_payload() -> None:
    existing = SimpleNamespace(payload={"chapter_id": "other"})
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[ScalarResult(), ScalarResult(existing)]),
        flush=AsyncMock(),
    )

    with pytest.raises(OutboxPayloadConflictError):
        await OutboxService.enqueue(db, "chapter.saved", "ch-1", 4, {"chapter_id": "ch-1"})


@pytest.mark.asyncio
async def test_lease_batch_uses_skip_locked() -> None:
    db = SimpleNamespace(execute=AsyncMock(return_value=ScalarResult(values=[])))

    assert await OutboxService.lease_batch(db, "dispatcher-1") == []
    assert "skip locked" in compiled_sql(db.execute.await_args.args[0])


@pytest.mark.asyncio
async def test_mark_sent_requires_active_lease_and_releases_it() -> None:
    db = SimpleNamespace(execute=AsyncMock(return_value=ScalarResult(rowcount=1)))

    assert await OutboxService.mark_sent(db, 12, "lease-token") is True
    statement = db.execute.await_args.args[0]
    sql = compiled_sql(statement)
    values = statement.compile().params

    assert "outbox_events.status" in sql
    assert "outbox_events.lease_token" in sql
    assert values["status"] == "sent"
    assert values["lease_owner"] is None
    assert values["lease_token"] is None
    assert values["lease_until"] is None


@pytest.mark.asyncio
async def test_mark_failed_locks_row_and_applies_backoff() -> None:
    event = SimpleNamespace(attempts=2)
    db = SimpleNamespace(execute=AsyncMock(side_effect=[ScalarResult(event), ScalarResult(rowcount=1)]))

    assert await OutboxService.mark_failed(db, 12, "lease-token", "temporary") is True
    select_sql = compiled_sql(db.execute.await_args_list[0].args[0])
    update_statement = db.execute.await_args_list[1].args[0]
    update_values = update_statement.compile().params

    assert "for update" in select_sql
    assert update_values["status"] == "pending"
    assert update_values["lease_token"] is None
    assert update_values["last_error"] == "temporary"


@pytest.mark.asyncio
async def test_mark_failed_moves_exhausted_event_to_dead_letter() -> None:
    available_at = object()
    event = SimpleNamespace(attempts=5, available_at=available_at)
    db = SimpleNamespace(execute=AsyncMock(side_effect=[ScalarResult(event), ScalarResult(rowcount=1)]))

    assert await OutboxService.mark_failed(db, 12, "lease-token", "permanent", max_attempts=5) is True
    values = db.execute.await_args_list[1].args[0].compile().params
    assert values["status"] == "dead_letter"
    assert values["available_at"] is available_at
