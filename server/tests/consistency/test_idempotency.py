"""Tests for canonical request hashing and two-phase idempotency."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from services.idempotency import IdempotencyConflictError, IdempotencyService


class TestCanonicalHash:
    """测试标准化哈希计算"""

    def test_same_payload_same_hash(self):
        """相同 payload 产生相同 hash"""
        payload1 = {"name": "测试", "value": 123, "items": [1, 2, 3]}
        payload2 = {"name": "测试", "value": 123, "items": [1, 2, 3]}

        hash1 = IdempotencyService.compute_canonical_hash(payload1)
        hash2 = IdempotencyService.compute_canonical_hash(payload2)

        assert hash1 == hash2

    def test_key_order_irrelevant(self):
        """字典 key 顺序不影响 hash"""
        payload1 = {"a": 1, "b": 2, "c": 3}
        payload2 = {"c": 3, "a": 1, "b": 2}
        payload3 = {"b": 2, "c": 3, "a": 1}

        hash1 = IdempotencyService.compute_canonical_hash(payload1)
        hash2 = IdempotencyService.compute_canonical_hash(payload2)
        hash3 = IdempotencyService.compute_canonical_hash(payload3)

        assert hash1 == hash2 == hash3

    def test_nested_dict_key_order(self):
        """嵌套字典 key 顺序不影响 hash"""
        payload1 = {"outer": {"b": 2, "a": 1}, "list": [{"y": 2, "x": 1}]}
        payload2 = {"outer": {"a": 1, "b": 2}, "list": [{"x": 1, "y": 2}]}

        hash1 = IdempotencyService.compute_canonical_hash(payload1)
        hash2 = IdempotencyService.compute_canonical_hash(payload2)

        assert hash1 == hash2

    def test_different_values_different_hash(self):
        """不同值产生不同 hash"""
        payload1 = {"key": "value1"}
        payload2 = {"key": "value2"}

        hash1 = IdempotencyService.compute_canonical_hash(payload1)
        hash2 = IdempotencyService.compute_canonical_hash(payload2)

        assert hash1 != hash2

    def test_unicode_preserved(self):
        """Unicode 字符正确处理"""
        payload1 = {"title": "第一章：雪夜叩关"}
        payload2 = {"title": "第一章：雪夜叩关"}
        payload3 = {"title": "Chapter 1: Different"}

        hash1 = IdempotencyService.compute_canonical_hash(payload1)
        hash2 = IdempotencyService.compute_canonical_hash(payload2)
        hash3 = IdempotencyService.compute_canonical_hash(payload3)

        assert hash1 == hash2
        assert hash1 != hash3

    def test_list_order_matters(self):
        """列表顺序影响 hash"""
        payload1 = {"items": [1, 2, 3]}
        payload2 = {"items": [3, 2, 1]}

        hash1 = IdempotencyService.compute_canonical_hash(payload1)
        hash2 = IdempotencyService.compute_canonical_hash(payload2)

        assert hash1 != hash2

    def test_null_values(self):
        """None 值正确处理"""
        payload1 = {"field": None}
        payload2 = {"field": None}
        payload3 = {"field": "value"}

        hash1 = IdempotencyService.compute_canonical_hash(payload1)
        hash2 = IdempotencyService.compute_canonical_hash(payload2)
        hash3 = IdempotencyService.compute_canonical_hash(payload3)

        assert hash1 == hash2
        assert hash1 != hash3

    def test_empty_collections(self):
        """空集合正确处理"""
        payload1 = {"list": [], "dict": {}}
        payload2 = {"list": [], "dict": {}}
        payload3 = {"list": [1], "dict": {}}

        hash1 = IdempotencyService.compute_canonical_hash(payload1)
        hash2 = IdempotencyService.compute_canonical_hash(payload2)
        hash3 = IdempotencyService.compute_canonical_hash(payload3)

        assert hash1 == hash2
        assert hash1 != hash3

    def test_numeric_types(self):
        """数值类型区分"""
        payload1 = {"value": 123}
        payload2 = {"value": 123.0}
        payload3 = {"value": "123"}

        hash1 = IdempotencyService.compute_canonical_hash(payload1)
        hash2 = IdempotencyService.compute_canonical_hash(payload2)
        hash3 = IdempotencyService.compute_canonical_hash(payload3)

        # JSON 中 123 和 123.0 序列化不同：123 vs 123.0
        assert hash1 != hash2
        # 字符串 "123" 也不同
        assert hash1 != hash3
        assert hash2 != hash3

    def test_boolean_values(self):
        """布尔值正确处理"""
        payload1 = {"flag": True}
        payload2 = {"flag": True}
        payload3 = {"flag": False}
        payload4 = {"flag": 1}  # JSON 中 1 != true

        hash1 = IdempotencyService.compute_canonical_hash(payload1)
        hash2 = IdempotencyService.compute_canonical_hash(payload2)
        hash3 = IdempotencyService.compute_canonical_hash(payload3)
        hash4 = IdempotencyService.compute_canonical_hash(payload4)

        assert hash1 == hash2
        assert hash1 != hash3
        assert hash1 != hash4

    def test_whitespace_in_strings(self):
        """字符串内空白影响 hash"""
        payload1 = {"text": "hello world"}
        payload2 = {"text": "hello  world"}  # 双空格
        payload3 = {"text": "helloworld"}

        hash1 = IdempotencyService.compute_canonical_hash(payload1)
        hash2 = IdempotencyService.compute_canonical_hash(payload2)
        hash3 = IdempotencyService.compute_canonical_hash(payload3)

        assert hash1 != hash2
        assert hash1 != hash3

    def test_complex_nested_structure(self):
        """复杂嵌套结构"""
        payload = {
            "chapter": {
                "title": "第一章",
                "outline": ["节点1", "节点2"],
                "metadata": {"author": "user_123", "tags": ["玄幻", "修真"]},
            },
            "options": {"auto_save": True, "version": 2},
        }

        hash1 = IdempotencyService.compute_canonical_hash(payload)
        hash2 = IdempotencyService.compute_canonical_hash(payload)

        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 产生 64 字符十六进制


class TestIdempotencyConflict:
    """测试幂等冲突异常"""

    def test_conflict_exception(self):
        """测试冲突异常属性"""
        exc = IdempotencyConflictError(
            scope="user_123:POST:/api",
            key="idem_key_456",
            expected_hash="abc123",
            actual_hash="def456",
        )

        assert exc.scope == "user_123:POST:/api"
        assert exc.key == "idem_key_456"
        assert exc.expected_hash == "abc123"
        assert exc.actual_hash == "def456"
        assert "Idempotency conflict" in str(exc)
        assert "abc123" in str(exc)
        assert "def456" in str(exc)


class ScalarResult:
    def __init__(self, value=None, rowcount=0):
        self.value = value
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value


def compiled_sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect())).lower()


@pytest.mark.asyncio
async def test_reserve_returns_execute_after_successful_insert() -> None:
    db = SimpleNamespace(execute=AsyncMock(return_value=ScalarResult(SimpleNamespace())))

    result = await IdempotencyService.reserve(db, "user:route", "key-1", {"title": "第一章"})

    assert result["action"] == "execute"
    assert result["owner_token"]
    assert "on conflict" in compiled_sql(db.execute.await_args.args[0])


@pytest.mark.asyncio
async def test_reserve_replays_completed_response() -> None:
    existing = SimpleNamespace(
        request_hash=IdempotencyService.compute_canonical_hash({"title": "第一章"}),
        status="completed",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        response_status=201,
        response_body={"revision": 3},
    )
    db = SimpleNamespace(execute=AsyncMock(side_effect=[ScalarResult(), ScalarResult(existing)]))

    result = await IdempotencyService.reserve(db, "user:route", "key-1", {"title": "第一章"})

    assert result == {"action": "replay", "status": 201, "body": {"revision": 3}}


@pytest.mark.asyncio
async def test_reserve_rejects_same_key_with_different_payload() -> None:
    existing = SimpleNamespace(request_hash="different")
    db = SimpleNamespace(execute=AsyncMock(side_effect=[ScalarResult(), ScalarResult(existing)]))

    with pytest.raises(IdempotencyConflictError):
        await IdempotencyService.reserve(db, "user:route", "key-1", {"title": "第一章"})


@pytest.mark.asyncio
async def test_reserve_waits_for_active_owner() -> None:
    existing = SimpleNamespace(
        request_hash=IdempotencyService.compute_canonical_hash({"title": "第一章"}),
        status="pending",
        lease_until=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    db = SimpleNamespace(execute=AsyncMock(side_effect=[ScalarResult(), ScalarResult(existing)]))

    result = await IdempotencyService.reserve(db, "user:route", "key-1", {"title": "第一章"})

    assert result == {"action": "wait"}


@pytest.mark.asyncio
async def test_expired_reservation_claim_is_atomic() -> None:
    existing = SimpleNamespace(
        request_hash=IdempotencyService.compute_canonical_hash({"title": "第一章"}),
        status="pending",
        lease_until=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[ScalarResult(), ScalarResult(existing), ScalarResult(rowcount=1)])
    )

    result = await IdempotencyService.reserve(db, "user:route", "key-1", {"title": "第一章"})
    claim_statement = db.execute.await_args_list[2].args[0]
    sql = compiled_sql(claim_statement)

    assert result["action"] == "execute"
    assert "lease_until is null" in sql
    assert "lease_until <=" in sql


@pytest.mark.asyncio
async def test_complete_requires_pending_record_and_owner_token() -> None:
    db = SimpleNamespace(execute=AsyncMock(return_value=ScalarResult(rowcount=1)))

    assert await IdempotencyService.complete(db, "user:route", "key-1", "owner", 200, {"ok": True})
    statement = db.execute.await_args.args[0]
    sql = compiled_sql(statement)
    values = statement.compile().params

    assert "idempotency_records.owner_token" in sql
    assert "idempotency_records.status" in sql
    assert values["status"] == "completed"
    assert values["owner_token"] is None
    assert values["lease_until"] is None
