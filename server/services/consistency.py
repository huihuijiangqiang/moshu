"""
Consistency service - extraction, claim management, and rule evaluation
"""
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_codex import CodexAlias, CodexEntry
from db.models_consistency_extended import ConsistencyClaim, ConsistencyRun


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


async def get_or_create_run(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    body_rev: int,
    pipeline_version: str,
    trigger: str,
) -> int:
    """获取或创建 consistency run 记录"""
    stmt = (
        insert(ConsistencyRun)
        .values(
            project_id=project_id,
            chapter_id=chapter_id,
            body_rev=body_rev,
            pipeline_version=pipeline_version,
            status="pending",
            trigger=trigger,
        )
        .on_conflict_do_nothing(index_elements=["chapter_id", "body_rev", "pipeline_version"])
        .returning(ConsistencyRun.id)
    )

    result = await db.execute(stmt)
    run_id = result.scalar_one_or_none()

    if run_id:
        return run_id

    # 冲突，查询已存在的
    select_stmt = select(ConsistencyRun.id).where(
        ConsistencyRun.chapter_id == chapter_id,
        ConsistencyRun.body_rev == body_rev,
        ConsistencyRun.pipeline_version == pipeline_version,
    )
    result = await db.execute(select_stmt)
    return result.scalar_one()


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
