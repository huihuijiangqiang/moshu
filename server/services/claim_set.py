"""同一版本的候选集替换：集合 diff，不删除、也不跳过。

一次抽取产出的是「这一版正文里的全部事实」——一个集合，不是一串增量。同一版本
重放（重试、pipeline 重跑、换模型）必须让库里的集合等于本次的集合，否则库里留下
的是两次运行的并集。

早期实现试过两种做法，都不成立：

* **删掉本版本的旧行再重插。** 违反架构 4.3「旧正文版本的 claim 不删除，标为
  superseded，确保告警可追溯」；而且 GuardIssueEvidence.claim_id 是
  ON DELETE SET NULL 的外键 —— 删 claim 会把已有告警的证据指针悄悄清空，作者
  看到一条没有出处的告警。
* **跳过已有指纹。** 幂等，但不是集合替换：上一次抽到、这一次没抽到的事实仍然
  留在当前版本里（作者删掉的那句话，告警还在报）；同一指纹的 entry_id、
  story_order、confidence 等重算结果也永远刷不进去。

正确做法是对 (chapter_id, body_rev, extractor_version, source_kind='body') 这个
范围做集合 diff：

* 本次存在 → 原地更新可变字段，并把 superseded 的行恢复成 accepted；
* 本次缺失 → 标 superseded（行还在，id 还在，证据指针还在）；
* 新出现   → 插入。

id 保持稳定是必须的：GuardIssueEvidence 按 claim_id 指过来，换一行就等于让历史
告警失去出处。

作者的处置不被覆盖：status='rejected' 是人做出的判断，重放不得把它改回
accepted，也不该把它降级成 superseded —— 那会让「作者否掉过这条」这个事实消失。
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_consistency_extended import ConsistencyClaim

#: 指纹相同 = 同一条事实的同一次出现（见 services.claim_identity）。但重算结果
#: 仍可能变化：实体链接解析出了 entry_id、时间线服务给出了 story_order、模型
#: 换了置信度。这些字段每次重放都要刷新，否则库里留的是第一次运行的快照。
#:
#: 身份字段（project_id / chapter_id / body_rev / source_kind /
#: extractor_version / fingerprint / predicate / object_type / polarity）不在此列：
#: 它们变了就不是同一条 claim，应当走「新出现」的插入路径。
MUTABLE_FIELDS = (
    "subject_entry_id",
    "object_entry_id",
    "subject_text",
    "object_value",
    "certainty",
    "paragraph_id",
    "source_anchor",
    "timeline_id",
    "temporal_anchor_text",
    "temporal_anchor_value",
    "temporal_event_ref",
    "temporal_relation",
    "temporal_relation_ref",
    "order_basis",
    "order_confidence",
    "story_order",
    "valid_from_order",
    "valid_to_order",
    "confidence",
)

#: 模型没给 confidence 时的取值，插入与更新必须一致 —— 否则重放会把已有行的
#: 置信度刷成 NULL。
DEFAULT_CONFIDENCE = 0.9

#: 作者已经否掉的 claim。重放既不恢复它，也不把它标 superseded。
AUTHOR_DECIDED_STATUSES = frozenset({"rejected"})


@dataclass
class ClaimSetDiff:
    """一次候选集替换的结果，用于任务返回值与可观测性。"""

    inserted: int = 0
    updated: int = 0
    restored: int = 0
    superseded: int = 0
    kept_rejected: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "restored": self.restored,
            "superseded": self.superseded,
            "kept_rejected": self.kept_rejected,
        }


def _mutable_values(claim_data: dict[str, Any]) -> dict[str, Any]:
    """从抽取结果里取出可变字段，缺省值与插入路径保持一致。"""
    values = {field: claim_data.get(field) for field in MUTABLE_FIELDS}
    if values["confidence"] is None:
        values["confidence"] = DEFAULT_CONFIDENCE
    if values["certainty"] is None:
        values["certainty"] = "explicit"
    return values


def _build_claim(
    claim_data: dict[str, Any],
    *,
    project_id: str,
    chapter_id: str,
    body_rev: int,
    extractor_version: str,
) -> ConsistencyClaim:
    return ConsistencyClaim(
        project_id=project_id,
        predicate=claim_data["predicate"],
        object_type=claim_data["object_type"],
        polarity=claim_data.get("polarity") or "positive",
        source_kind="body",
        chapter_id=chapter_id,
        body_rev=body_rev,
        extractor_version=extractor_version,
        fingerprint=claim_data["fingerprint"],
        status="accepted",
        **_mutable_values(claim_data),
    )


async def _load_current_set(
    db: AsyncSession,
    *,
    chapter_id: str,
    body_rev: int,
    extractor_version: str,
) -> list[ConsistencyClaim]:
    result = await db.execute(
        select(ConsistencyClaim)
        .where(
            ConsistencyClaim.chapter_id == chapter_id,
            ConsistencyClaim.source_kind == "body",
            ConsistencyClaim.body_rev == body_rev,
            ConsistencyClaim.extractor_version == extractor_version,
        )
        .order_by(ConsistencyClaim.id)
    )
    return list(result.scalars().all())


async def replace_body_claim_set(
    db: AsyncSession,
    *,
    project_id: str,
    chapter_id: str,
    body_rev: int,
    extractor_version: str,
    claims: list[dict[str, Any]],
) -> ClaimSetDiff:
    """把本版本的 claim 集合替换成 `claims`，一行都不删。

    范围就是唯一键 uq_claim_body_source 的范围：同一 (chapter_id, body_rev,
    fingerprint, extractor_version)。因此不会插入已存在的指纹，也就不会撞唯一键。

    调用方负责事务：本函数只 flush，不 commit —— 集合替换必须与版本校验、
    旧版本 supersede 在同一个事务里落库。
    """
    incoming: dict[str, dict[str, Any]] = {}
    for claim_data in claims:
        # 上游 provider 已按指纹聚合过；这里再挡一次，保持「先出现的赢」的确定顺序
        incoming.setdefault(claim_data["fingerprint"], claim_data)

    existing_rows = await _load_current_set(
        db, chapter_id=chapter_id, body_rev=body_rev, extractor_version=extractor_version
    )

    # 指纹 -> 规范行。PostgreSQL 上部分唯一索引保证不会有重复，SQLite 上没有这层
    # 保护；真出现重复时取 id 最小的那行为准（它才是证据指过来的那行），其余按
    # 「本次缺失」处理标 superseded，而不是留下两条并存的重复事实。
    canonical: dict[str, ConsistencyClaim] = {}
    duplicates: list[ConsistencyClaim] = []
    for row in existing_rows:
        if row.fingerprint in canonical:
            duplicates.append(row)
        else:
            canonical[row.fingerprint] = row

    diff = ClaimSetDiff()
    now = datetime.now(timezone.utc)

    # 本次存在：原地更新，并把上一轮标掉的行恢复成 accepted
    for fingerprint, claim_data in incoming.items():
        row = canonical.get(fingerprint)
        if row is None:
            db.add(
                _build_claim(
                    claim_data,
                    project_id=project_id,
                    chapter_id=chapter_id,
                    body_rev=body_rev,
                    extractor_version=extractor_version,
                )
            )
            diff.inserted += 1
            continue

        _refresh(row, claim_data, now)
        diff.updated += 1

        if row.status in AUTHOR_DECIDED_STATUSES:
            # 作者否掉过这条：字段照刷，判断不动
            diff.kept_rejected += 1
            continue
        if row.status == "superseded":
            diff.restored += 1
        row.status = "accepted"

    # 本次缺失：标 superseded —— 行、id、证据指针都留着
    missing = [row for fingerprint, row in canonical.items() if fingerprint not in incoming]
    for row in missing + duplicates:
        if row.status in AUTHOR_DECIDED_STATUSES:
            # 作者的否决比 superseded 更具体，是一条独立的信息，保留它
            diff.kept_rejected += 1
            continue
        if row.status == "superseded":
            continue
        row.status = "superseded"
        row.updated_at = now
        diff.superseded += 1

    await db.flush()
    return diff


def _refresh(row: ConsistencyClaim, claim_data: dict[str, Any], now: datetime) -> None:
    """刷新重算出来的字段。身份字段不动 —— 它们变了就不是同一条 claim。"""
    for field, value in _mutable_values(claim_data).items():
        setattr(row, field, value)
    row.updated_at = now


__all__ = [
    "AUTHOR_DECIDED_STATUSES",
    "DEFAULT_CONFIDENCE",
    "MUTABLE_FIELDS",
    "ClaimSetDiff",
    "replace_body_claim_set",
]
