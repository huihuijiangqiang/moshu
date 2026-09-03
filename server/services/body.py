"""
Chapter body save service - with versioning, content_hash, idempotency, and outbox
"""
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_codex import CodexEntry, CodexRef
from db.models_core import Chapter, ChapterBody, ChapterVersion
from services.idempotency import IdempotencyService
from services.outbox import OutboxService
from services.provenance import sync_accepted_words


class ChapterNotFoundError(LookupError):
    pass


class BodyRevisionConflictError(RuntimeError):
    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__(f"body revision conflict: expected={expected}, actual={actual}")


class InvalidParagraphStructureError(ValueError):
    pass


class InvalidCodexRefError(ValueError):
    pass


def compute_content_hash(content_json: dict) -> str:
    """计算内容哈希 - 标准化 JSON"""
    canonical = json.dumps(content_json, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def extract_paragraph_ids(content_json: dict) -> set[str]:
    """提取所有可定位块的 pid - 递归覆盖 paragraph/heading/listItem"""
    pids = set()

    def walk_nodes(nodes):
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_type = node.get("type")
            if node_type in ("paragraph", "heading", "listItem"):
                pid = node.get("attrs", {}).get("pid")
                if pid:
                    pids.add(pid)
            if "content" in node:
                walk_nodes(node["content"])

    walk_nodes(content_json.get("content", []))
    return pids


def validate_paragraph_structure(content_json: dict) -> None:
    """验证段落结构：每个可定位块必须有非空唯一 pid"""
    if not isinstance(content_json, dict):
        raise InvalidParagraphStructureError("content_json must be a dict")

    content = content_json.get("content", [])
    if not isinstance(content, list):
        raise InvalidParagraphStructureError("content_json.content must be a list")

    pids = []
    has_locatable_block = False

    def walk_nodes(nodes):
        nonlocal has_locatable_block
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_type = node.get("type")
            if node_type in ("paragraph", "heading", "listItem"):
                has_locatable_block = True
                pid = node.get("attrs", {}).get("pid")
                if not pid or not isinstance(pid, str) or not pid.strip():
                    raise InvalidParagraphStructureError(
                        f"Every {node_type} must have a non-empty pid attribute"
                    )
                pids.append(pid)
            if "content" in node:
                walk_nodes(node["content"])

    walk_nodes(content)

    if not has_locatable_block:
        raise InvalidParagraphStructureError("content_json must contain at least one locatable block")

    if len(pids) != len(set(pids)):
        raise InvalidParagraphStructureError("Duplicate pid found in locatable blocks")


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
                    refs.append({"entry_id": entry_id})
            if "content" in node:
                walk_nodes(node["content"])
            if "marks" in node:
                for mark in node.get("marks", []):
                    if isinstance(mark, dict) and mark.get("type") == "codexRef":
                        entry_id = mark.get("attrs", {}).get("entryId")
                        if entry_id:
                            refs.append({"entry_id": entry_id})

    walk_nodes(content_json.get("content", []))
    return refs


def count_words(content_json: dict) -> int:
    """统计字数 - 提取所有文本节点"""
    text_parts = []

    def walk_nodes(nodes):
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("type") == "text":
                text = node.get("text", "")
                if text:
                    text_parts.append(text)
            if "content" in node:
                walk_nodes(node["content"])

    walk_nodes(content_json.get("content", []))
    full_text = "".join(text_parts)
    return len(full_text.strip())


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

    Args:
        db: 数据库会话
        chapter_id: 章节 ID
        base_rev: 基准版本（0 表示新建）
        content_html: HTML 内容
        content_json: JSON 内容
        idempotency_key: 幂等键（可选）
        trigger: 触发来源

    Returns:
        {
            "chapter_id": str,
            "rev": int,
            "content_hash": str,
            "consistency_status": str  # "queued" | "unchanged" | "skipped"
        }

    Raises:
        ChapterNotFoundError: 章节不存在
        BodyRevisionConflictError: 版本冲突
        InvalidParagraphStructureError: 段落结构无效
        InvalidCodexRefError: CodexRef 引用无效
        IdempotencyConflictError: 幂等键冲突（同 key 不同 payload）
    """
    # 1. 验证段落结构
    validate_paragraph_structure(content_json)

    # 2. 计算 content_hash
    content_hash = compute_content_hash(content_json)

    # 3. 计算 request_hash（包含所有影响保存结果的输入）
    request_payload = {
        "chapter_id": chapter_id,
        "base_rev": base_rev,
        "content_hash": content_hash,
        "trigger": trigger,
    }

    # 4. 幂等检查（如果提供了 idempotency_key）
    owner_token = None
    if idempotency_key:
        scope = f"save_body:{chapter_id}"
        reservation = await IdempotencyService.reserve(
            db,
            scope=scope,
            key=idempotency_key,
            request_payload=request_payload,
            ttl_hours=1,
            lease_seconds=300,
        )

        if reservation["action"] == "replay":
            # 重放已完成的响应
            return reservation["body"]
        elif reservation["action"] == "wait":
            # 请求正在处理中，返回 202 提示客户端稍后重试
            raise IdempotencyInProgressError(scope, idempotency_key)
        else:
            # action == "execute"
            owner_token = reservation["owner_token"]

    # 5. 锁定章节和正文行
    chapter_result = await db.execute(
        select(Chapter)
        .where(Chapter.id == chapter_id, Chapter.deleted_at.is_(None))
        .with_for_update()
    )
    chapter = chapter_result.scalar_one_or_none()
    if chapter is None:
        raise ChapterNotFoundError(chapter_id)

    body_result = await db.execute(
        select(ChapterBody).where(ChapterBody.chapter_id == chapter_id).with_for_update()
    )
    body = body_result.scalar_one_or_none()

    # 6. 验证 base_rev
    current_rev = body.rev if body else 0
    if base_rev != current_rev:
        raise BodyRevisionConflictError(base_rev, current_rev)

    # 7. 内容未变化检查
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
            response = {
                "chapter_id": chapter_id,
                "rev": body.rev,
                "content_hash": content_hash,
                "consistency_status": "unchanged",
            }
            if idempotency_key and owner_token:
                await IdempotencyService.complete(
                    db,
                    scope=f"save_body:{chapter_id}",
                    key=idempotency_key,
                    owner_token=owner_token,
                    response_status=200,
                    response_body=response,
                )
            return response

    new_rev = current_rev + 1
    now = datetime.now(timezone.utc)

    # 8. 更新或创建 chapter_body
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

    # 9. 更新 words
    words = count_words(content_json)
    chapter.words = words

    # 10. 创建版本快照
    version = ChapterVersion(
        chapter_id=chapter_id,
        content_html=content_html,
        content_json=content_json,
        rev=new_rev,
        trigger=trigger,
        content_hash=content_hash,
    )
    db.add(version)

    # 11. 重建显式 CodexRef（聚合计数，验证有效性）
    # 先删除旧的
    delete_result = await db.execute(
        select(CodexRef).where(CodexRef.chapter_id == chapter_id)
    )
    for ref in delete_result.scalars():
        await db.delete(ref)

    # 提取并验证新的
    codex_refs = extract_codex_refs(content_json)
    if codex_refs:
        # 统计每个 entry_id 出现次数
        entry_counts: dict[str, int] = {}
        for ref_data in codex_refs:
            entry_id = ref_data["entry_id"]
            entry_counts[entry_id] = entry_counts.get(entry_id, 0) + 1

        # 验证 entry_id 存在且属于同一项目
        if entry_counts:
            entry_result = await db.execute(
                select(CodexEntry.id, CodexEntry.project_id)
                .where(CodexEntry.id.in_(list(entry_counts.keys())))
            )
            valid_entries = {row.id: row.project_id for row in entry_result}

            # 检查是否有跨项目或无效的引用
            invalid_refs = []
            for entry_id in entry_counts.keys():
                if entry_id not in valid_entries:
                    invalid_refs.append(f"{entry_id} (not found)")
                elif valid_entries[entry_id] != chapter.project_id:
                    invalid_refs.append(f"{entry_id} (wrong project: {valid_entries[entry_id]})")

            if invalid_refs:
                raise InvalidCodexRefError(
                    f"Invalid or cross-project CodexRef entries: {', '.join(invalid_refs)}"
                )

            # 写入有效的引用
            for entry_id, count in entry_counts.items():
                if entry_id in valid_entries:
                    ref = CodexRef(
                        entry_id=entry_id,
                        chapter_id=chapter_id,
                        count=count,
                    )
                    db.add(ref)

    # Recalculate the north-star metric from server-verified generation fingerprints.
    await sync_accepted_words(
        db,
        project_id=chapter.project_id,
        chapter_id=chapter_id,
        content_json=content_json,
    )

    # 12. 写 transactional outbox
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

    response = {
        "chapter_id": chapter_id,
        "rev": new_rev,
        "content_hash": content_hash,
        "consistency_status": "queued",
    }

    # 13. 完成幂等记录
    if idempotency_key and owner_token:
        await IdempotencyService.complete(
            db,
            scope=f"save_body:{chapter_id}",
            key=idempotency_key,
            owner_token=owner_token,
            response_status=200,
            response_body=response,
        )

    return response


class IdempotencyInProgressError(RuntimeError):
    def __init__(self, scope: str, key: str):
        self.scope = scope
        self.key = key
        super().__init__(f"Idempotency request in progress: scope={scope}, key={key}")
