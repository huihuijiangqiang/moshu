"""
Chapter body save service - with versioning, content_hash, and outbox
"""
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_codex import CodexEntry, CodexRef
from db.models_core import Chapter, ChapterBody, ChapterVersion
from services.outbox import OutboxService


class ChapterNotFoundError(LookupError):
    pass


class BodyRevisionConflictError(RuntimeError):
    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__(f"body revision conflict: expected={expected}, actual={actual}")


def compute_content_hash(content_json: dict) -> str:
    """计算内容哈希 - 标准化 JSON"""
    canonical = json.dumps(content_json, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def extract_paragraph_ids(content_json: dict) -> set[str]:
    """提取所有段落 ID"""
    pids = set()
    if not isinstance(content_json, dict):
        return pids

    content = content_json.get("content", [])
    if not isinstance(content, list):
        return pids

    for node in content:
        if isinstance(node, dict) and node.get("type") == "paragraph":
            pid = node.get("attrs", {}).get("pid")
            if pid:
                pids.add(pid)

    return pids


def extract_codex_refs(content_json: dict) -> list[dict]:
    """提取所有 CodexRef 标记"""
    refs = []

    def walk_nodes(nodes):
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("type") == "codexRef":
                attrs = node.get("attrs", {})
                entry_id = attrs.get("entryId")
                if entry_id:
                    refs.append({"entry_id": entry_id, "attrs": attrs})
            if "content" in node:
                walk_nodes(node["content"])
            if "marks" in node:
                for mark in node.get("marks", []):
                    if isinstance(mark, dict) and mark.get("type") == "codexRef":
                        entry_id = mark.get("attrs", {}).get("entryId")
                        if entry_id:
                            refs.append({"entry_id": entry_id, "attrs": mark.get("attrs", {})})

    walk_nodes(content_json.get("content", []))
    return refs


async def save_chapter_body(
    db: AsyncSession,
    *,
    chapter_id: str,
    base_rev: int,
    content_html: str,
    content_json: dict,
    idempotency_key: Optional[str] = None,
    trigger: str = "manual",
) -> dict:
    """
    保存章节正文 - 严格版本控制、内容哈希、显式 CodexRef 重建、outbox 事件

    Returns:
        {"chapter_id": str, "rev": int, "content_hash": str, "consistency_status": str}
    """
    # 1. 锁定章节和正文行
    chapter_result = await db.execute(
        select(Chapter).where(Chapter.id == chapter_id).with_for_update()
    )
    chapter = chapter_result.scalar_one_or_none()
    if chapter is None:
        raise ChapterNotFoundError(chapter_id)

    body_result = await db.execute(
        select(ChapterBody).where(ChapterBody.chapter_id == chapter_id).with_for_update()
    )
    body = body_result.scalar_one_or_none()

    # 2. 验证 base_rev
    current_rev = body.rev if body else 0
    if base_rev != current_rev:
        raise BodyRevisionConflictError(base_rev, current_rev)

    # 3. 计算 content_hash 并验证段落 ID
    content_hash = compute_content_hash(content_json)
    paragraph_ids = extract_paragraph_ids(content_json)

    if not paragraph_ids:
        raise ValueError("content_json must contain paragraphs with stable pid attributes")

    # 4. 幂等检查（如果提供了 idempotency_key，这里应该用 IdempotencyService）
    # 简化版：直接查最新版本的 hash
    if body:
        latest_version = await db.execute(
            select(ChapterVersion)
            .where(ChapterVersion.chapter_id == chapter_id)
            .order_by(ChapterVersion.rev.desc())
            .limit(1)
        )
        latest = latest_version.scalar_one_or_none()
        if latest and latest.content_hash == content_hash:
            # 内容未变化，返回当前版本
            return {
                "chapter_id": chapter_id,
                "rev": body.rev,
                "content_hash": content_hash,
                "consistency_status": "unchanged",
            }

    new_rev = current_rev + 1
    now = datetime.now(timezone.utc)

    # 5. 更新或创建 chapter_body
    if body is None:
        body = ChapterBody(
            chapter_id=chapter_id,
            content_html=content_html,
            content_json=content_json,
            rev=new_rev,
        )
        db.add(body)
    else:
        body.content_html = content_html
        body.content_json = content_json
        body.rev = new_rev
        body.updated_at = now

    # 6. 创建版本快照
    version = ChapterVersion(
        chapter_id=chapter_id,
        content_html=content_html,
        content_json=content_json,
        rev=new_rev,
        trigger=trigger,
        content_hash=content_hash,
    )
    db.add(version)

    # 7. 重建显式 CodexRef
    # 先删除旧的
    await db.execute(
        select(CodexRef)
        .where(CodexRef.chapter_id == chapter_id)
        .with_for_update()
    )
    delete_result = await db.execute(
        select(CodexRef).where(CodexRef.chapter_id == chapter_id)
    )
    for ref in delete_result.scalars():
        await db.delete(ref)

    # 提取并验证新的
    codex_refs = extract_codex_refs(content_json)
    valid_entry_ids = set()
    if codex_refs:
        entry_result = await db.execute(
            select(CodexEntry.id)
            .where(CodexEntry.id.in_([r["entry_id"] for r in codex_refs]))
            .where(CodexEntry.project_id == chapter.project_id)
        )
        valid_entry_ids = {row[0] for row in entry_result}

    for ref_data in codex_refs:
        entry_id = ref_data["entry_id"]
        if entry_id in valid_entry_ids:
            ref = CodexRef(
                entry_id=entry_id,
                chapter_id=chapter_id,
                ref_type="explicit",
                paragraph_id=ref_data["attrs"].get("paragraphId"),
                confidence=1.0,
            )
            db.add(ref)

    # 8. 写 transactional outbox
    await OutboxService.enqueue(
        db,
        topic="chapter.body_saved",
        aggregate_id=chapter_id,
        aggregate_rev=new_rev,
        payload={
            "project_id": chapter.project_id,
            "chapter_id": chapter_id,
            "body_rev": new_rev,
            "content_hash": content_hash,
            "trigger": trigger,
        },
    )

    await db.flush()

    return {
        "chapter_id": chapter_id,
        "rev": new_rev,
        "content_hash": content_hash,
        "consistency_status": "queued",
    }
