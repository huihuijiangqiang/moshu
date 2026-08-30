"""
测试 - 幂等性服务
"""
import pytest

from services.idempotency import IdempotencyConflict, IdempotencyService


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

        # JSON 中 123 和 123.0 相同
        assert hash1 == hash2
        # 但字符串 "123" 不同
        assert hash1 != hash3

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
        exc = IdempotencyConflict(
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
