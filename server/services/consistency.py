"""
Consistency service - extraction, claim management, and rule evaluation
"""
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_codex import CodexAlias, CodexEntry
from db.models_consistency_extended import ConsistencyClaim, ConsistencyRun, DocumentSummary

#: 一致性管道版本。API、Celery 任务与 ConsistencyRun 的唯一键必须共用同一个值，
#: 否则写入用一个版本、查询用另一个版本，状态查询会永远 404。
PIPELINE_VERSION = "1.0.0"

#: 规则扫描版本，写入 GuardIssue.rule_version。
RULE_VERSION = "1.0.0"

#: claim 抽取器版本，参与 claim 唯一键。
EXTRACTOR_VERSION = "1.0.0"

#: 摘要版本，参与 DocumentSummary 唯一键。
SUMMARY_VERSION = "1.0.0"

#: 抽取结果没给 timeline_id 时使用的主时间线。
#:
#: 三条 P0 规则都按 (subject, timeline_id) 分组，并且要求 story_order 非空才
#: 参与排序判断。timeline_id 留 NULL、story_order 留 NULL 的 claim 永远不会
#: 被任何规则看到 —— 落库了却检不出冲突，等于白抽。
DEFAULT_TIMELINE_ID = "main"

#: ConsistencyRun.trigger 的 CHECK 约束允许的取值。
VALID_RUN_TRIGGERS = frozenset({"body_save", "manual_scan", "pipeline_upgrade", "maintenance"})

#: 上游事件里的 trigger 名称 -> ConsistencyRun.trigger 合法取值。
_TRIGGER_ALIASES = {
    "user_edit": "body_save",
    "manual": "body_save",
    "autosave": "body_save",
    "accept_draft": "body_save",
    "body_saved": "body_save",
}


def normalize_run_trigger(raw_trigger: Optional[str]) -> str:
    """把事件里的 trigger 映射到 ConsistencyRun.trigger 的合法取值。

    直接写入未映射的值会撞上 ck_consistency_run_trigger CHECK 约束。已经合法的
    值（如 manual_scan）必须原样保留 —— 早期实现无条件返回 body_save，手动扫描
    与流水线升级触发的 run 全被记成了正文保存。
    """
    if not raw_trigger:
        return "body_save"
    trigger = raw_trigger.strip().lower()
    if trigger in VALID_RUN_TRIGGERS:
        return trigger
    return _TRIGGER_ALIASES.get(trigger, "body_save")


def compute_claim_fingerprint(
    subject_text: str,
    predicate: str,
    object_type: str,
    object_value: Optional[str],
    polarity: str,
) -> str:
    """计算 claim 指纹 - 用于去重"""
    normalized = json.dumps(
        {
            "subject": subject_text.strip().lower(),
            "predicate": predicate,
            "object_type": object_type,
            "object_value": object_value.strip().lower() if object_value else None,
            "polarity": polarity,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


#: 状态型谓词：同一主体的后一条声明会终止前一条的有效区间。
#: 规则用 valid_from_order / valid_to_order 判定区间重叠，不闭合区间就永远重叠。
STATEFUL_PREDICATES = frozenset({"alive", "owns", "owned_by", "located_at", "has_ability"})

#: 这些谓词的「状态」是按对象区分的：一个人同时拥有剑和盾并不冲突，
#: 所以闭合区间时必须把对象一起纳入分组键。
#: 而 alive / located_at 的对象是状态值本身，后一条直接取代前一条。
OBJECT_SCOPED_PREDICATES = frozenset({"owns", "owned_by", "has_ability"})


def derive_story_order(chapter_idx: int, ordinal: int, total: int) -> float:
    """把「章内第 ordinal 条声明」映射成全书叙事顺序。

    Chapter.idx 是步长 1024 的稀疏序号，所以把章内偏移压进 (idx, idx+1) 区间即可
    保证：同章内严格递增，且永远不会越到下一章前面。

    story_order 是 Numeric(24,8)，8 位小数足够容纳一章内的数千条声明。
    """
    if total <= 0:
        raise ValueError("total must be positive")
    if not 0 <= ordinal < total:
        raise ValueError(f"ordinal {ordinal} out of range for total {total}")
    return round(chapter_idx + (ordinal + 1) / (total + 1), 8)


def _interval_group_key(claim: dict) -> tuple:
    """闭合有效区间时的分组键。"""
    subject = claim.get("subject_entry_id") or f"text:{(claim.get('subject_text') or '').strip().lower()}"
    predicate = claim.get("predicate")
    if predicate in OBJECT_SCOPED_PREDICATES:
        object_key = claim.get("object_entry_id") or (claim.get("object_value") or "").strip().lower()
        return (subject, predicate, object_key)
    return (subject, predicate)


def assign_narrative_positions(
    claims: list[dict],
    *,
    chapter_idx: int,
    timeline_id: str = DEFAULT_TIMELINE_ID,
) -> list[dict]:
    """给一章抽取出的 claim 补齐时间线与叙事位置字段。

    抽取器只给出文本内容，timeline_id / story_order / valid_from_order /
    valid_to_order 全是 NULL。而三条 P0 规则都要求 story_order 非空、按
    (subject, timeline_id) 分组 —— 不补这些字段，claim 落库了也检不出任何冲突。

    模型若自己给了值就沿用（尊重更精确的抽取结果），否则按章内顺序推导。
    状态型谓词的有效区间在同组内前后闭合：前一条的 valid_to_order 设为后一条的
    valid_from_order，最后一条保持开区间（None = 一直有效）。

    返回的是新的 dict 列表，不修改入参。
    """
    total = len(claims)
    positioned = []
    for ordinal, claim in enumerate(claims):
        enriched = dict(claim)
        enriched.setdefault("timeline_id", None)
        if not enriched.get("timeline_id"):
            enriched["timeline_id"] = timeline_id
        if enriched.get("story_order") is None:
            enriched["story_order"] = derive_story_order(chapter_idx, ordinal, total)
        if enriched.get("valid_from_order") is None and enriched.get("predicate") in STATEFUL_PREDICATES:
            enriched["valid_from_order"] = enriched["story_order"]
        positioned.append(enriched)

    # 闭合同组内相邻状态区间
    groups: dict[tuple, list[dict]] = {}
    for claim in positioned:
        if claim.get("predicate") not in STATEFUL_PREDICATES:
            continue
        groups.setdefault((claim["timeline_id"],) + _interval_group_key(claim), []).append(claim)

    for group in groups.values():
        ordered = sorted(group, key=lambda c: c["story_order"])
        for earlier, later in zip(ordered, ordered[1:]):
            if earlier.get("valid_to_order") is None:
                earlier["valid_to_order"] = later["valid_from_order"]

    return positioned


async def _select_run_id(
    db: AsyncSession,
    *,
    chapter_id: str,
    body_rev: int,
    pipeline_version: str,
) -> Optional[int]:
    result = await db.execute(
        select(ConsistencyRun.id).where(
            ConsistencyRun.chapter_id == chapter_id,
            ConsistencyRun.body_rev == body_rev,
            ConsistencyRun.pipeline_version == pipeline_version,
        )
    )
    return result.scalar_one_or_none()


async def get_or_create_run(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    body_rev: int,
    pipeline_version: str,
    trigger: str,
) -> int:
    """获取或创建 consistency run 记录（并发安全、方言无关）。

    唯一键是 uq_consistency_run_key(chapter_id, body_rev, pipeline_version)：
    两个 worker 同时处理同一次保存时，「先查后插」之间存在竞态窗口，后插的一方
    会撞唯一键。这里把插入放进 SAVEPOINT，撞键后回滚该 SAVEPOINT 并改读已存在
    的行 —— 外层事务不受影响，调用方拿到的始终是同一个 run_id。

    不用 PostgreSQL 的 ON CONFLICT：那条语句在 SQLite 上无法编译，会让这段逻辑
    彻底无法被单元测试覆盖。
    """
    existing = await _select_run_id(
        db, chapter_id=chapter_id, body_rev=body_rev, pipeline_version=pipeline_version
    )
    if existing is not None:
        return existing

    run = ConsistencyRun(
        project_id=project_id,
        chapter_id=chapter_id,
        body_rev=body_rev,
        pipeline_version=pipeline_version,
        status="pending",
        trigger=normalize_run_trigger(trigger),
        started_at=datetime.now(timezone.utc),
    )
    try:
        async with db.begin_nested():
            db.add(run)
            await db.flush()
    except IntegrityError:
        # 并发插入已经建好了同一个 run；读回它而不是把错误抛给调用方
        raced = await _select_run_id(
            db, chapter_id=chapter_id, body_rev=body_rev, pipeline_version=pipeline_version
        )
        if raced is None:
            raise
        return raced

    return run.id


async def _select_summary_row(
    db: AsyncSession,
    *,
    owner_type: str,
    owner_id: str,
    source_rev: int,
    summary_version: str,
) -> Optional[DocumentSummary]:
    """按唯一键读回摘要行；找不到返回 None。"""
    result = await db.execute(
        select(DocumentSummary).where(
            DocumentSummary.owner_type == owner_type,
            DocumentSummary.owner_id == owner_id,
            DocumentSummary.source_rev == source_rev,
            DocumentSummary.summary_version == summary_version,
        )
    )
    return result.scalar_one_or_none()


async def upsert_active_summary(
    db: AsyncSession,
    *,
    owner_type: str,
    owner_id: str,
    source_rev: int,
    summary_version: str,
    content: str,
    model_id: str,
    token_count: Optional[int],
) -> DocumentSummary:
    """写入/更新某个来源版本的摘要行（并发安全、方言无关）。

    唯一键是 uq_document_summary_key(owner_type, owner_id, source_rev,
    summary_version)。同版本重跑必须原地更新而不是再插一行，并发插入撞键时读回
    已存在的行继续更新 —— 否则重试一次就把整个任务打成 IntegrityError。
    """
    key = {
        "owner_type": owner_type,
        "owner_id": owner_id,
        "source_rev": source_rev,
        "summary_version": summary_version,
    }
    existing = await _select_summary_row(db, **key)

    if existing is None:
        summary = DocumentSummary(
            **key,
            content=content,
            model_id=model_id,
            token_count=token_count,
            status="active",
        )
        try:
            async with db.begin_nested():
                db.add(summary)
                await db.flush()
            return summary
        except IntegrityError:
            # 另一个 worker 抢先插入了同一个键。SAVEPOINT 回滚已把我们这行逐出会话，
            # 读回它那行继续更新，而不是把整个任务打成失败。
            existing = await _select_summary_row(db, **key)
            if existing is None:
                raise

    existing.content = content
    existing.model_id = model_id
    existing.token_count = token_count
    existing.status = "active"
    existing.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return existing


async def resolve_entity_by_alias(
    db: AsyncSession,
    project_id: str,
    text: str,
) -> Optional[str]:
    """通过精确别名解析实体 - Unicode 规范化"""
    # NFC 规范化并去除首尾空白
    import unicodedata

    normalized = unicodedata.normalize("NFC", text.strip())

    # 精确匹配
    stmt = (
        select(CodexEntry.id)
        .join(CodexAlias, CodexAlias.entry_id == CodexEntry.id)
        .where(CodexEntry.project_id == project_id)
        .where(CodexAlias.alias == normalized)
        .limit(1)
    )
    result = await db.execute(stmt)
    entry_id = result.scalar_one_or_none()
    return entry_id


async def upsert_claim(
    db: AsyncSession,
    *,
    project_id: str,
    subject_text: str,
    predicate: str,
    object_type: str,
    object_value: Optional[str],
    polarity: str = "positive",
    certainty: str = "explicit",
    source_kind: str,
    chapter_id: Optional[str] = None,
    body_rev: Optional[int] = None,
    outline_rev: Optional[int] = None,
    paragraph_id: Optional[str] = None,
    timeline_id: Optional[str] = None,
    story_order: Optional[float] = None,
    extractor_version: str,
    confidence: Optional[float] = None,
) -> int:
    """插入或更新 claim - 幂等"""
    # 尝试解析实体
    subject_entry_id = await resolve_entity_by_alias(db, project_id, subject_text)

    # 计算指纹
    fingerprint = compute_claim_fingerprint(subject_text, predicate, object_type, object_value, polarity)

    # Upsert
    stmt = (
        insert(ConsistencyClaim)
        .values(
            project_id=project_id,
            subject_entry_id=subject_entry_id,
            subject_text=subject_text,
            predicate=predicate,
            object_type=object_type,
            object_value=object_value,
            polarity=polarity,
            certainty=certainty,
            source_kind=source_kind,
            chapter_id=chapter_id,
            body_rev=body_rev,
            outline_rev=outline_rev,
            paragraph_id=paragraph_id,
            timeline_id=timeline_id,
            story_order=story_order,
            extractor_version=extractor_version,
            confidence=confidence,
            fingerprint=fingerprint,
            status="candidate",
        )
        .on_conflict_do_update(
            index_elements=["source_kind", "chapter_id", "body_rev", "outline_rev", "fingerprint", "extractor_version"],
            set_={
                "subject_entry_id": subject_entry_id,
                "object_value": object_value,
                "confidence": confidence,
                "status": "candidate",
                "updated_at": datetime.now(timezone.utc),
            },
        )
        .returning(ConsistencyClaim.id)
    )

    result = await db.execute(stmt)
    return result.scalar_one()


async def supersede_old_claims(
    db: AsyncSession,
    *,
    chapter_id: str,
    old_body_rev: int,
    extractor_version: str,
) -> int:
    """将旧版本的 claim 标记为 superseded"""
    stmt = (
        update(ConsistencyClaim)
        .where(
            ConsistencyClaim.chapter_id == chapter_id,
            ConsistencyClaim.body_rev == old_body_rev,
            ConsistencyClaim.extractor_version == extractor_version,
            ConsistencyClaim.status == "candidate",
        )
        .values(status="superseded", updated_at=datetime.now(timezone.utc))
    )
    result = await db.execute(stmt)
    return result.rowcount


async def check_alive_conflict(
    db: AsyncSession,
    project_id: str,
    entry_id: str,
    timeline_id: str,
    valid_from: float,
    valid_to: Optional[float],
) -> list[dict]:
    """检查生死冲突 - P0 规则 1"""
    # 查找该实体在时间线上的所有 alive 状态 claim
    stmt = (
        select(ConsistencyClaim)
        .where(
            ConsistencyClaim.project_id == project_id,
            ConsistencyClaim.subject_entry_id == entry_id,
            ConsistencyClaim.predicate == "alive",
            ConsistencyClaim.timeline_id == timeline_id,
            ConsistencyClaim.status.in_(["candidate", "accepted"]),
        )
        .order_by(ConsistencyClaim.story_order)
    )
    result = await db.execute(stmt)
    claims = list(result.scalars().all())

    conflicts = []
    for claim in claims:
        # 如果是 alive=false，且在有效区间内
        if claim.object_value == "false" and claim.polarity == "positive":
            if claim.valid_from_order is not None:
                # 检查区间重叠
                overlap = False
                if valid_to is None:
                    overlap = claim.valid_from_order >= valid_from
                else:
                    overlap = claim.valid_from_order < valid_to and (
                        claim.valid_to_order is None or claim.valid_to_order > valid_from
                    )

                if overlap:
                    conflicts.append(
                        {
                            "type": "alive_conflict",
                            "entity_id": entry_id,
                            "conflicting_claim_id": claim.id,
                            "timeline_id": timeline_id,
                        }
                    )

    return conflicts


async def check_ownership_conflict(
    db: AsyncSession,
    project_id: str,
    item_entry_id: str,
    owner_entry_id: str,
    timeline_id: str,
    story_order: float,
) -> list[dict]:
    """检查物品归属冲突 - P0 规则 2"""
    # 查找该物品在相同时间点的其他归属 claim
    stmt = (
        select(ConsistencyClaim)
        .where(
            ConsistencyClaim.project_id == project_id,
            ConsistencyClaim.subject_entry_id == item_entry_id,
            ConsistencyClaim.predicate == "owned_by",
            ConsistencyClaim.timeline_id == timeline_id,
            ConsistencyClaim.status.in_(["candidate", "accepted"]),
        )
        .order_by(ConsistencyClaim.story_order)
    )
    result = await db.execute(stmt)
    claims = list(result.scalars().all())

    conflicts = []
    for claim in claims:
        # 如果在相同或相近时间点，但归属不同
        if claim.object_entry_id and claim.object_entry_id != owner_entry_id:
            if claim.story_order is not None and abs(claim.story_order - story_order) < 0.001:
                conflicts.append(
                    {
                        "type": "ownership_conflict",
                        "item_id": item_entry_id,
                        "owner_id": owner_entry_id,
                        "conflicting_owner_id": claim.object_entry_id,
                        "conflicting_claim_id": claim.id,
                    }
                )

    return conflicts


async def check_knowledge_boundary(
    db: AsyncSession,
    project_id: str,
    character_entry_id: str,
    fact_text: str,
    timeline_id: str,
    story_order: float,
) -> list[dict]:
    """检查知情边界 - P0 规则 3"""
    # 查找该角色何时获知该事实
    stmt = (
        select(ConsistencyClaim)
        .where(
            ConsistencyClaim.project_id == project_id,
            ConsistencyClaim.subject_entry_id == character_entry_id,
            ConsistencyClaim.predicate == "knows_fact",
            ConsistencyClaim.timeline_id == timeline_id,
            ConsistencyClaim.status.in_(["candidate", "accepted"]),
        )
        .order_by(ConsistencyClaim.story_order)
    )
    result = await db.execute(stmt)
    claims = list(result.scalars().all())

    conflicts = []
    for claim in claims:
        # 如果事实相同，但时间顺序早于当前
        if claim.object_value and claim.object_value == fact_text:
            if claim.story_order is not None and claim.story_order < story_order:
                # 不冲突 - 角色已经知道
                pass
            elif claim.story_order is not None and claim.story_order > story_order:
                # 冲突 - 角色在未来才知道，但现在就表现出知情
                conflicts.append(
                    {
                        "type": "knowledge_boundary_violation",
                        "character_id": character_entry_id,
                        "fact": fact_text,
                        "knows_at_order": claim.story_order,
                        "used_at_order": story_order,
                        "conflicting_claim_id": claim.id,
                    }
                )

    return conflicts
