"""
Tests for body save service - versioning, content_hash, outbox
"""

from services.body import (
    compute_content_hash,
    extract_codex_refs,
    extract_paragraph_ids,
)


class TestContentHash:
    """测试内容哈希计算"""

    def test_same_content_same_hash(self):
        """相同内容产生相同哈希"""
        content1 = {"type": "doc", "content": [{"type": "paragraph", "attrs": {"pid": "p1"}}]}
        content2 = {"type": "doc", "content": [{"type": "paragraph", "attrs": {"pid": "p1"}}]}

        hash1 = compute_content_hash(content1)
        hash2 = compute_content_hash(content2)

        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 hex

    def test_key_order_irrelevant(self):
        """键顺序不影响哈希"""
        content1 = {"content": [], "type": "doc"}
        content2 = {"type": "doc", "content": []}

        hash1 = compute_content_hash(content1)
        hash2 = compute_content_hash(content2)

        assert hash1 == hash2

    def test_different_content_different_hash(self):
        """不同内容产生不同哈希"""
        content1 = {"type": "doc", "content": [{"type": "paragraph", "attrs": {"pid": "p1"}}]}
        content2 = {"type": "doc", "content": [{"type": "paragraph", "attrs": {"pid": "p2"}}]}

        hash1 = compute_content_hash(content1)
        hash2 = compute_content_hash(content2)

        assert hash1 != hash2

    def test_unicode_preserved(self):
        """Unicode 字符保持原样"""
        content1 = {"text": "测试"}
        content2 = {"text": "测试"}

        hash1 = compute_content_hash(content1)
        hash2 = compute_content_hash(content2)

        assert hash1 == hash2


class TestParagraphExtraction:
    """测试段落 ID 提取"""

    def test_extract_single_paragraph(self):
        """提取单个段落"""
        content = {
            "type": "doc",
            "content": [{"type": "paragraph", "attrs": {"pid": "p1"}}],
        }

        pids = extract_paragraph_ids(content)
        assert pids == {"p1"}

    def test_extract_multiple_paragraphs(self):
        """提取多个段落"""
        content = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "attrs": {"pid": "p1"}},
                {"type": "paragraph", "attrs": {"pid": "p2"}},
                {"type": "heading", "attrs": {"level": 1}},
                {"type": "paragraph", "attrs": {"pid": "p3"}},
            ],
        }

        pids = extract_paragraph_ids(content)
        assert pids == {"p1", "p2", "p3"}

    def test_ignore_paragraphs_without_pid(self):
        """忽略没有 pid 的段落"""
        content = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "attrs": {"pid": "p1"}},
                {"type": "paragraph", "attrs": {}},
                {"type": "paragraph"},
            ],
        }

        pids = extract_paragraph_ids(content)
        assert pids == {"p1"}

    def test_empty_content(self):
        """空内容返回空集合"""
        content = {"type": "doc", "content": []}
        pids = extract_paragraph_ids(content)
        assert pids == set()


class TestCodexRefExtraction:
    """测试 CodexRef 提取"""

    def test_extract_node_level_ref(self):
        """提取节点级 CodexRef"""
        content = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "看到了"},
                        {"type": "codexRef", "attrs": {"entryId": "ent_123", "paragraphId": "p1"}},
                        {"type": "text", "text": "走过来"},
                    ],
                }
            ],
        }

        refs = extract_codex_refs(content)
        assert len(refs) == 1
        assert refs[0]["entry_id"] == "ent_123"

    def test_extract_mark_level_ref(self):
        """提取标记级 CodexRef"""
        content = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "沈砚",
                            "marks": [{"type": "codexRef", "attrs": {"entryId": "ent_456"}}],
                        }
                    ],
                }
            ],
        }

        refs = extract_codex_refs(content)
        assert len(refs) == 1
        assert refs[0]["entry_id"] == "ent_456"

    def test_extract_multiple_refs(self):
        """提取多个 CodexRef"""
        content = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "沈砚",
                            "marks": [{"type": "codexRef", "attrs": {"entryId": "ent_1"}}],
                        },
                        {"type": "text", "text": "和"},
                        {
                            "type": "text",
                            "text": "苏清",
                            "marks": [{"type": "codexRef", "attrs": {"entryId": "ent_2"}}],
                        },
                    ],
                }
            ],
        }

        refs = extract_codex_refs(content)
        assert len(refs) == 2
        assert refs[0]["entry_id"] == "ent_1"
        assert refs[1]["entry_id"] == "ent_2"

    def test_no_refs(self):
        """无 CodexRef 返回空列表"""
        content = {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "纯文本"}]}],
        }

        refs = extract_codex_refs(content)
        assert refs == []

    def test_ignore_refs_without_entry_id(self):
        """忽略没有 entryId 的 ref"""
        content = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "codexRef", "attrs": {}},
                        {"type": "codexRef", "attrs": {"entryId": "ent_123"}},
                    ],
                }
            ],
        }

        refs = extract_codex_refs(content)
        assert len(refs) == 1
        assert refs[0]["entry_id"] == "ent_123"
