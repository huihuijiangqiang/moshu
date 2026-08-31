"""
Extended consistency models - P0-B consistency data structures
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin

#: 管道的三个阶段。抽取先跑，摘要与扫描随后**并行**。
RUN_PHASES: tuple[str, ...] = ("extract", "summary", "scan")

#: 每个阶段各自的状态取值。
RUN_PHASE_STATES: tuple[str, ...] = ("pending", "running", "succeeded", "failed")

#: 阶段状态列名，顺序与 RUN_PHASES 一致。
RUN_PHASE_COLUMNS: dict[str, str] = {phase: f"{phase}_state" for phase in RUN_PHASES}


def _phase_state_check(phase: str) -> CheckConstraint:
    """阶段状态列的取值约束（名字里带列名，违约时能直接定位到阶段）。"""
    column = RUN_PHASE_COLUMNS[phase]
    values = ", ".join(f"'{state}'" for state in RUN_PHASE_STATES)
    return CheckConstraint(f"{column} IN ({values})", name=f"ck_consistency_run_{column}")


def _completed_requires_all_phases() -> CheckConstraint:
    """completed 必须蕴含三个阶段全部成功 —— 由数据库兜底，不靠调用方自觉。

    摘要与扫描并行，任一支线单独成功都不足以说明这一版正文处理完了。以前 status
    只跟踪扫描支线，扫描一成功就写 completed/finished_at，摘要还在跑（甚至随后失败）
    也照样显示完成 —— 上下文构建方据此去读根本不存在的摘要。
    """
    succeeded = " AND ".join(
        f"{RUN_PHASE_COLUMNS[phase]} = 'succeeded'" for phase in RUN_PHASES
    )
    return CheckConstraint(
        f"status <> 'completed' OR ({succeeded})",
        name="ck_consistency_run_completed_phases",
    )


class ConsistencyRun(Base, TimestampMixin):
    """记录一次章节正文版本的一致性处理状态。

    status 是给人看的粗粒度进度（pending → extracting → summarizing/scanning →
    completed/failed）；真正的完成判定靠 extract_state / summary_state / scan_state
    三个独立阶段列，它们才是并行支线的可靠状态机。
    """

    __tablename__ = "consistency_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[str] = mapped_column(String(32), ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    body_rev: Mapped[int] = mapped_column(Integer, nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), server_default="pending", nullable=False, index=True)
    # 三条支线各自的状态：completed / failed 都由它们推导，不再靠某一支线抢先写入
    extract_state: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", nullable=False
    )
    summary_state: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", nullable=False
    )
    scan_state: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", nullable=False
    )
    trigger: Mapped[str] = mapped_column(String(50), nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("chapter_id", "body_rev", "pipeline_version", name="uq_consistency_run_key"),
        CheckConstraint(
            "status IN ('pending', 'extracting', 'summarizing', 'scanning', 'completed', 'failed')",
            name="ck_consistency_run_status",
        ),
        CheckConstraint(
            "trigger IN ('body_save', 'manual_scan', 'pipeline_upgrade', 'maintenance')",
            name="ck_consistency_run_trigger",
        ),
        CheckConstraint("body_rev > 0", name="ck_consistency_run_body_rev_positive"),
        _phase_state_check("extract"),
        _phase_state_check("summary"),
        _phase_state_check("scan"),
        _completed_requires_all_phases(),
    )


class DocumentSummary(Base, TimestampMixin):
    """版本化章节/卷摘要"""

    __tablename__ = "document_summaries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_rev: Mapped[int] = mapped_column(Integer, nullable=False)
    summary_version: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    covered_chapter_from: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    covered_chapter_to: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)
    token_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), server_default="active", nullable=False)

    __table_args__ = (
        UniqueConstraint("owner_type", "owner_id", "source_rev", "summary_version", name="uq_document_summary_key"),
        CheckConstraint("owner_type IN ('chapter', 'volume')", name="ck_summary_owner_type"),
        CheckConstraint("status IN ('active', 'stale', 'superseded')", name="ck_summary_status"),
        CheckConstraint("source_rev > 0", name="ck_summary_source_rev_positive"),
        Index("ix_document_summary_owner", "owner_type", "owner_id", "status"),
    )


class ConsistencyClaim(Base, TimestampMixin):
    """结构化事实声称 - 来源版本绑定"""

    __tablename__ = "consistency_claims"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    subject_entry_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("codex_entries.id", ondelete="SET NULL"), nullable=True, index=True
    )
    subject_text: Mapped[str] = mapped_column(String(200), nullable=False)
    predicate: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    object_type: Mapped[str] = mapped_column(String(50), nullable=False)
    object_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    object_entry_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("codex_entries.id", ondelete="SET NULL"), nullable=True
    )
    polarity: Mapped[str] = mapped_column(String(20), server_default="positive", nullable=False)
    certainty: Mapped[str] = mapped_column(String(20), server_default="explicit", nullable=False)
    source_kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    chapter_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=True, index=True
    )
    body_rev: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    outline_rev: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    paragraph_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    #: 稳定的来源锚点：这条事实在**整章**里的全局段落号（见 services.chunking）。
    #: 与 timeline_id 一起构成 claim 的身份（见 services.claim_identity），并让
    #: 告警能指回正文的具体位置 —— paragraph_id 来自编辑器，抽取时看不到。
    source_anchor: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    timeline_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    story_order: Mapped[Optional[float]] = mapped_column(Numeric(24, 8), nullable=True)
    valid_from_order: Mapped[Optional[float]] = mapped_column(Numeric(24, 8), nullable=True)
    valid_to_order: Mapped[Optional[float]] = mapped_column(Numeric(24, 8), nullable=True)
    extractor_version: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(Numeric(5, 4), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), server_default="candidate", nullable=False, index=True)

    __table_args__ = (
        CheckConstraint("polarity IN ('positive', 'negative')", name="ck_claim_polarity"),
        CheckConstraint("certainty IN ('explicit', 'inferred', 'uncertain')", name="ck_claim_certainty"),
        CheckConstraint(
            "source_kind IN ('codex', 'body', 'outline', 'resolution')",
            name="ck_claim_source_kind"
        ),
        CheckConstraint(
            "status IN ('candidate', 'accepted', 'rejected', 'superseded')",
            name="ck_claim_status"
        ),
        CheckConstraint(
            "object_type IN ('scalar', 'entity', 'location', 'ability', 'timestamp')",
            name="ck_claim_object_type"
        ),
        CheckConstraint(
            "(source_kind = 'body' AND chapter_id IS NOT NULL AND body_rev IS NOT NULL) OR "
            "(source_kind = 'outline' AND chapter_id IS NOT NULL AND outline_rev IS NOT NULL) OR "
            "(source_kind IN ('codex', 'resolution'))",
            name="ck_claim_source_versioning"
        ),
        Index(
            "uq_claim_body_source",
            "chapter_id", "body_rev", "fingerprint", "extractor_version",
            unique=True,
            postgresql_where="source_kind = 'body'"
        ),
        Index(
            "uq_claim_outline_source",
            "chapter_id", "outline_rev", "fingerprint", "extractor_version",
            unique=True,
            postgresql_where="source_kind = 'outline'"
        ),
        Index(
            "uq_claim_codex_source",
            "fingerprint", "extractor_version",
            unique=True,
            postgresql_where="source_kind = 'codex'"
        ),
        Index(
            "uq_claim_resolution_source",
            "fingerprint", "extractor_version",
            unique=True,
            postgresql_where="source_kind = 'resolution'"
        ),
        Index("ix_claim_subject_predicate", "subject_entry_id", "predicate", "status"),
        Index("ix_claim_timeline_order", "timeline_id", "story_order"),
    )


class StoryEvent(Base, TimestampMixin):
    """故事事件 - 时间线与叙事位置"""

    __tablename__ = "story_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    timeline_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    story_order: Mapped[Optional[float]] = mapped_column(Numeric(24, 8), nullable=True)
    time_text: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    time_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    time_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    chapter_id: Mapped[str] = mapped_column(String(32), ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    body_rev: Mapped[int] = mapped_column(Integer, nullable=False)
    paragraph_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), server_default="candidate", nullable=False, index=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('candidate', 'confirmed', 'rejected', 'superseded')",
            name="ck_event_status"
        ),
        CheckConstraint("body_rev > 0", name="ck_event_body_rev_positive"),
        Index("ix_event_timeline_order", "timeline_id", "story_order"),
        Index("ix_event_chapter", "chapter_id", "body_rev"),
    )


class EntityStateInterval(Base, TimestampMixin):
    """实体状态区间 - SQL 可校验"""

    __tablename__ = "entity_state_intervals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    entry_id: Mapped[str] = mapped_column(String(32), ForeignKey("codex_entries.id", ondelete="CASCADE"), index=True)
    state_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    value_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    valid_from_order: Mapped[float] = mapped_column(Numeric(24, 8), nullable=False)
    valid_to_order: Mapped[Optional[float]] = mapped_column(Numeric(24, 8), nullable=True)
    timeline_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_claim_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("consistency_claims.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), server_default="candidate", nullable=False, index=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('candidate', 'accepted', 'rejected', 'superseded')",
            name="ck_state_interval_status"
        ),
        CheckConstraint(
            "valid_to_order IS NULL OR valid_to_order > valid_from_order",
            name="ck_state_interval_order_valid"
        ),
        Index("ix_state_interval_timeline", "timeline_id", "valid_from_order", "valid_to_order"),
        Index("ix_state_interval_entry_key", "entry_id", "state_key", "status"),
    )


class GuardIssueEvidence(Base, TimestampMixin):
    """告警证据 - 版本化锚点"""

    __tablename__ = "guard_issue_evidence"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    issue_id: Mapped[str] = mapped_column(String(32), ForeignKey("guard_issues.id", ondelete="CASCADE"), index=True)
    side: Mapped[str] = mapped_column(String(20), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    chapter_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=True
    )
    body_rev: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    outline_rev: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    codex_entry_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("codex_entries.id", ondelete="SET NULL"), nullable=True
    )
    paragraph_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    start_offset: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    end_offset: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    offset_encoding: Mapped[str] = mapped_column(String(20), server_default="utf16", nullable=False)
    quote: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    quote_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    anchor_version: Mapped[str] = mapped_column(String(20), server_default="v1", nullable=False)
    claim_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("consistency_claims.id", ondelete="SET NULL"), nullable=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("side IN ('expected', 'actual', 'context')", name="ck_evidence_side"),
        CheckConstraint(
            "source_kind IN ('codex', 'body', 'outline', 'claim')",
            name="ck_evidence_source_kind"
        ),
        CheckConstraint(
            "offset_encoding IN ('utf8', 'utf16', 'codepoint')",
            name="ck_evidence_offset_encoding"
        ),
        CheckConstraint(
            "(source_kind = 'body' AND chapter_id IS NOT NULL AND body_rev IS NOT NULL) OR "
            "(source_kind = 'outline' AND chapter_id IS NOT NULL AND outline_rev IS NOT NULL) OR "
            "(source_kind = 'codex' AND codex_entry_id IS NOT NULL) OR "
            "(source_kind = 'claim' AND claim_id IS NOT NULL)",
            name="ck_evidence_source_required"
        ),
        Index("ix_evidence_issue_side", "issue_id", "side", "sort_order"),
    )


class GuardResolution(Base, TimestampMixin):
    """告警处置记录"""

    __tablename__ = "guard_resolutions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    issue_id: Mapped[str] = mapped_column(String(32), ForeignKey("guard_issues.id", ondelete="CASCADE"), index=True)
    issue_rev: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"))

    __table_args__ = (
        CheckConstraint(
            "action IN ('accept_old_fact', 'accept_new_fact', 'intentional_exception', "
            "'false_positive', 'fixed_in_body', 'defer')",
            name="ck_resolution_action"
        ),
        CheckConstraint("issue_rev > 0", name="ck_resolution_issue_rev_positive"),
        Index("ix_resolution_issue", "issue_id", "created_at"),
    )
