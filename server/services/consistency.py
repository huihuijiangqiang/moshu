"""
Consistency service - extraction, claim management, and rule evaluation
"""
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_codex import CodexAlias, CodexEntry
from db.models_consistency_extended import ConsistencyClaim, ConsistencyRun, DocumentSummary
from services.timeline import is_globally_anchored

#: 一致性管道版本。API、Celery 任务与 ConsistencyRun 的唯一键必须共用同一个值，
#: 否则写入用一个版本、查询用另一个版本，状态查询会永远 404。
PIPELINE_VERSION = "1.0.0"

#: 规则扫描版本，写入 GuardIssue.rule_version。
RULE_VERSION = "1.0.0"

#: claim 抽取器版本，参与 claim 唯一键。
EXTRACTOR_VERSION = "1.0.0"

#: 摘要版本，参与 DocumentSummary 唯一键。
SUMMARY_VERSION = "1.0.0"

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

def is_order_reliable(claim: dict) -> bool:
    """判断这条 claim 的叙事顺序能否用于依赖时序的硬规则。

    架构 4.3：「无法从正文可靠确定顺序时保持为空，只进入待确认列表，不运行依赖
    时序的硬规则。」

    可靠的判据**不在这里**，而在 services.timeline：只有具备全局可比锚点的
    claim 才会被分配 story_order。这里要做的是确认那一步已经发生 —— story_order
    非空且锚点确实全局可比。两个条件都查，是因为 story_order 也可能来自旧数据。

    局部序号（order_basis="narration_local"）永远过不了这一关：它在块内自洽，
    跨块无意义，而规则是跨块比较的。
    """
    if claim.get("story_order") is None:
        return False
    return is_globally_anchored(claim)


def _interval_group_key(claim: dict) -> tuple:
    """闭合有效区间时的分组键。"""
    subject = claim.get("subject_entry_id") or f"text:{(claim.get('subject_text') or '').strip().lower()}"
    predicate = claim.get("predicate")
    if predicate in OBJECT_SCOPED_PREDICATES:
        object_key = claim.get("object_entry_id") or (claim.get("object_value") or "").strip().lower()
        return (subject, predicate, object_key)
    return (subject, predicate)


def assign_narrative_positions(claims: list[dict]) -> list[dict]:
    """闭合可靠且同时间线的状态区间；顺序不可靠的 claim 一律保持 NULL。

    **不推导 story_order。** 早期实现把 Chapter.idx 压成 story_order，违反架构
    4.3：story_order 是故事世界中的事件顺序，不是「第几章」。插叙、倒叙、多线
    叙事里两者会背离 —— 用章节序号顶替，会把正常的倒叙判成「先死后活」这类
    高等级时序矛盾，而作者根本无法解释这条告警。伪造顺序只是让规则「命中」，
    并不代表检出了真实冲突。

    因此这里只做一件安全的事：对**已经可靠**的顺序（见 is_order_reliable）在
    同一 timeline_id 内前后闭合有效区间。不可靠的 claim 保留 story_order=None，
    照常落库进入待确认列表，但不参与依赖时序的硬规则。

    timeline_id 也不再兜底填 "main"：把两条独立叙事线强行并进同一条时间线，
    等于制造跨线比较，而架构要求「只有存在已确认的跨线锚点时才比较」。

    返回新的 dict 列表，不修改入参。
    """
    positioned = [dict(claim) for claim in claims]

    for claim in positioned:
        claim.setdefault("timeline_id", None)
        claim.setdefault("story_order", None)
        claim.setdefault("valid_from_order", None)
        claim.setdefault("valid_to_order", None)

        if not is_order_reliable(claim):
            # 顺序不可信 —— 连区间起点都不能给，否则 ownership 规则会拿它兜底比较
            claim["story_order"] = None
            claim["valid_from_order"] = None
            claim["valid_to_order"] = None
            continue

        if claim.get("valid_from_order") is None and claim.get("predicate") in STATEFUL_PREDICATES:
            claim["valid_from_order"] = claim["story_order"]

    # 只在「顺序可靠 + 同一 timeline_id 非空」的组内闭合区间。
    # timeline_id 为空表示叙事线未知，不能与任何 claim 比较先后。
    groups: dict[tuple, list[dict]] = {}
    for claim in positioned:
        if claim.get("predicate") not in STATEFUL_PREDICATES:
            continue
        if not is_order_reliable(claim) or not claim.get("timeline_id"):
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


#: 每种 source_kind 对应的部分唯一索引推断参数。
#:
#: claim 的唯一性不是一个索引，而是四个按 source_kind 分区的**部分**唯一索引
#: （见 ConsistencyClaim.__table_args__）。ON CONFLICT 的冲突推断必须同时给出
#: 索引列**和** where 谓词，PostgreSQL 才能匹配到具体那个部分索引。
#:
#: 早期实现写的是 index_elements=[source_kind, chapter_id, body_rev,
#: outline_rev, fingerprint, extractor_version] —— 这六列上没有任何索引，
#: PostgreSQL 会直接报
#:   InvalidColumnReference: there is no unique or exclusion constraint
#:   matching the ON CONFLICT specification
#: 也就是说这个函数在生产上一次都不可能成功。SQLite 不编译 ON CONFLICT，
#: 单元测试永远碰不到，真实拒绝行为只能由 PostgreSQL 集成测试覆盖。
_CLAIM_CONFLICT_TARGETS = {
    "body": (
        ["chapter_id", "body_rev", "fingerprint", "extractor_version"],
        "source_kind = 'body'",
    ),
    "outline": (
        ["chapter_id", "outline_rev", "fingerprint", "extractor_version"],
        "source_kind = 'outline'",
    ),
    "codex": (["fingerprint", "extractor_version"], "source_kind = 'codex'"),
    "resolution": (["fingerprint", "extractor_version"], "source_kind = 'resolution'"),
}


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
    """插入或更新 claim（幂等，PostgreSQL 专用）。

    冲突目标按 source_kind 选取对应的部分唯一索引，见 _CLAIM_CONFLICT_TARGETS。
    """
    if source_kind not in _CLAIM_CONFLICT_TARGETS:
        raise ValueError(
            f"unknown source_kind {source_kind!r}; expected one of "
            f"{sorted(_CLAIM_CONFLICT_TARGETS)}"
        )
    index_elements, index_where = _CLAIM_CONFLICT_TARGETS[source_kind]

    subject_entry_id = await resolve_entity_by_alias(db, project_id, subject_text)
    fingerprint = compute_claim_fingerprint(
        subject_text, predicate, object_type, object_value, polarity
    )

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
            index_elements=index_elements,
            index_where=sa_text(index_where),
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
