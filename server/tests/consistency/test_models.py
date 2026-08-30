"""
测试 - 模型约束和默认值
"""
import pytest
from sqlalchemy.exc import IntegrityError

from db.models_consistency import (
    ChapterOutlineRevision,
    ChapterOutlineState,
    IdempotencyRecord,
    OutboxEvent,
)


class TestChapterOutlineState:
    """测试章纲状态模型"""

    def test_default_values(self):
        """测试默认值"""
        state = ChapterOutlineState(chapter_id="ch_test")
        assert state.revision == 0
        assert state.note == ""
        assert state.body_needs_revision is False
        assert state.marked_outline_rev is None
        assert state.marked_body_rev is None

    def test_marked_revisions_nullable(self):
        """测试标记版本可为空"""
        state = ChapterOutlineState(
            chapter_id="ch_test",
            body_needs_revision=True,
            marked_outline_rev=5,
            marked_body_rev=10,
        )
        assert state.marked_outline_rev == 5
        assert state.marked_body_rev == 10

        state2 = ChapterOutlineState(
            chapter_id="ch_test2",
            body_needs_revision=False,
        )
        assert state2.marked_outline_rev is None
        assert state2.marked_body_rev is None


class TestChapterOutlineRevision:
    """测试章纲历史快照模型"""

    def test_required_fields(self):
        """测试必填字段"""
        revision = ChapterOutlineRevision(
            chapter_id="ch_test",
            revision=1,
            title="测试章节",
            nodes=["节点1", "节点2"],
            note="备注",
            body_policy="plan_only",
            created_by="user_123",
        )
        assert revision.chapter_id == "ch_test"
        assert revision.revision == 1
        assert revision.title == "测试章节"
        assert revision.nodes == ["节点1", "节点2"]
        assert revision.body_policy == "plan_only"

    def test_body_policy_values(self):
        """测试 body_policy 枚举值"""
        # plan_only
        rev1 = ChapterOutlineRevision(
            chapter_id="ch_1",
            revision=1,
            title="Title",
            nodes=[],
            body_policy="plan_only",
            created_by="user",
        )
        assert rev1.body_policy == "plan_only"

        # mark_body_for_revision
        rev2 = ChapterOutlineRevision(
            chapter_id="ch_2",
            revision=1,
            title="Title",
            nodes=[],
            body_policy="mark_body_for_revision",
            created_by="user",
        )
        assert rev2.body_policy == "mark_body_for_revision"

    def test_empty_nodes_allowed(self):
        """测试允许空节点列表"""
        revision = ChapterOutlineRevision(
            chapter_id="ch_test",
            revision=1,
            title="无大纲章节",
            nodes=[],
            body_policy="plan_only",
            created_by="user",
        )
        assert revision.nodes == []

    def test_body_rev_at_change_nullable(self):
        """测试 body_rev_at_change 可为空"""
        # 无正文时为 None
        rev1 = ChapterOutlineRevision(
            chapter_id="ch_1",
            revision=1,
            title="Title",
            nodes=[],
            body_policy="plan_only",
            body_rev_at_change=None,
            created_by="user",
        )
        assert rev1.body_rev_at_change is None

        # 有正文时记录版本
        rev2 = ChapterOutlineRevision(
            chapter_id="ch_2",
            revision=1,
            title="Title",
            nodes=[],
            body_policy="mark_body_for_revision",
            body_rev_at_change=5,
            created_by="user",
        )
        assert rev2.body_rev_at_change == 5


class TestOutboxEvent:
    """测试 Outbox 事件模型"""

    def test_default_values(self):
        """测试默认值"""
        event = OutboxEvent(
            topic="test.topic",
            aggregate_id="agg_123",
            aggregate_rev=1,
            payload={"key": "value"},
        )
        assert event.status == "pending"
        assert event.attempts == 0
        assert event.lease_owner is None
        assert event.lease_token is None
        assert event.lease_until is None
        assert event.sent_at is None
        assert event.last_error is None

    def test_status_values(self):
        """测试状态枚举值"""
        statuses = ["pending", "dispatching", "sent", "failed", "dead_letter"]
        for status in statuses:
            event = OutboxEvent(
                topic="test",
                aggregate_id="123",
                aggregate_rev=1,
                payload={},
                status=status,
            )
            assert event.status == status

    def test_payload_jsonb(self):
        """测试 payload JSONB 字段"""
        payload = {
            "task_name": "extract_chapter",
            "args": {"chapter_id": "ch_123", "body_rev": 5},
            "nested": {"data": [1, 2, 3]},
        }
        event = OutboxEvent(
            topic="chapter.saved",
            aggregate_id="ch_123",
            aggregate_rev=5,
            payload=payload,
        )
        assert event.payload == payload
        assert event.payload["args"]["body_rev"] == 5

    def test_lease_fields(self):
        """测试租约相关字段"""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        event = OutboxEvent(
            topic="test",
            aggregate_id="123",
            aggregate_rev=1,
            payload={},
            status="dispatching",
            lease_owner="worker-01",
            lease_token="token_abc",
            lease_until=now,
        )
        assert event.lease_owner == "worker-01"
        assert event.lease_token == "token_abc"
        assert event.lease_until == now


class TestIdempotencyRecord:
    """测试幂等记录模型"""

    def test_required_fields(self):
        """测试必填字段"""
        from datetime import datetime, timedelta, timezone

        expires = datetime.now(timezone.utc) + timedelta(hours=24)
        record = IdempotencyRecord(
            scope="user_123:POST:/chapters",
            key="idem_key_456",
            request_hash="abc123def456",
            response_status=201,
            response_body={"id": "ch_789", "revision": 1},
            expires_at=expires,
        )
        assert record.scope == "user_123:POST:/chapters"
        assert record.key == "idem_key_456"
        assert record.request_hash == "abc123def456"
        assert record.response_status == 201
        assert record.response_body["id"] == "ch_789"
        assert record.expires_at == expires

    def test_response_body_jsonb(self):
        """测试响应体 JSONB"""
        from datetime import datetime, timedelta, timezone

        response = {
            "success": True,
            "data": {"chapter_id": "ch_123", "outline_revision": 5},
            "errors": None,
        }
        record = IdempotencyRecord(
            scope="test",
            key="key",
            request_hash="hash",
            response_status=200,
            response_body=response,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        assert record.response_body == response
        assert record.response_body["data"]["outline_revision"] == 5
