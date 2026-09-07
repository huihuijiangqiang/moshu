"""
风格、用量、占比模型 - 4张表
"""
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from db.models_core import Project, User


class StyleProfile(Base, TimestampMixin):
    """风格指纹表 - 属于用户，不属于项目"""

    __tablename__ = "style_profiles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    sample_words: Mapped[int] = mapped_column(Integer)
    sample_text: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(default=False)
    dimensions: Mapped[dict] = mapped_column(JSONB)  # 6个维度：句长/对白/修饰/意象/钩子/口头禅
    alignment: Mapped[float] = mapped_column(default=0.0)  # 最近生成的对齐度评分
    upload_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # 对象存储URL
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extracted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "uq_style_profiles_default_user",
            "user_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    # 关系
    user: Mapped["User"] = relationship()


class GenerationRun(Base):
    """生成运行记录表 - 北极星指标的数据来源"""

    __tablename__ = "generation_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[str] = mapped_column(String(32), ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    task_type: Mapped[str] = mapped_column(String(50))  # chapter, inline, wizard
    model_tier: Mapped[str] = mapped_column(String(20))  # cheap, main, premium
    prompt_tokens: Mapped[int] = mapped_column(Integer)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)  # prompt cache命中量
    completion_tokens: Mapped[int] = mapped_column(Integer)
    generated_words: Mapped[int] = mapped_column(Integer)
    accepted_words: Mapped[int] = mapped_column(Integer, default=0)  # 北极星指标
    layer_report: Mapped[dict] = mapped_column(JSONB)  # 四层装配的实际token数
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # 关系
    user: Mapped["User"] = relationship()
    project: Mapped["Project"] = relationship()


class GenerationDraft(Base, TimestampMixin):
    """AI candidate text kept outside the canonical chapter body."""

    __tablename__ = "generation_drafts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("generation_runs.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[str] = mapped_column(String(32), ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="streaming", index=True)
    content_text: Mapped[str] = mapped_column(Text, default="")
    generated_words: Mapped[int] = mapped_column(Integer, default=0)
    request_summary: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("kind IN ('chapter', 'inline')", name="ck_generation_draft_kind"),
        CheckConstraint(
            "status IN ('streaming', 'ready', 'failed', 'accepted', 'rejected')",
            name="ck_generation_draft_status",
        ),
        Index("ix_generation_drafts_chapter_status_created", "chapter_id", "status", "created_at"),
    )


class UsageLog(Base):
    """Immutable billing event with an explicit reservation lifecycle."""

    __tablename__ = "usage_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_id: Mapped[Optional[str]] = mapped_column(
        String(32),
        ForeignKey("generation_runs.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )
    platform_event_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    provider_requests: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    usage_estimated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    feature: Mapped[str] = mapped_column(String(50))  # generate_chapter, inline, guard, export等
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reserved_credits: Mapped[int] = mapped_column(Integer, default=0)
    credits: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="completed", index=True)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    reservation_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    finalized_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Platform events and author billing reservations use separate nullable
        # identifiers; a platform event is always keyed, author rows stay NULL.
        Index("uq_usage_logs_platform_event_id", "platform_event_id", unique=True),
        CheckConstraint("provider_requests > 0", name="ck_usage_logs_provider_requests_positive"),
    )

    # 关系
    user: Mapped["User"] = relationship()


class BillingProduct(Base, TimestampMixin):
    """A purchasable credit package or recurring plan snapshot source."""

    __tablename__ = "billing_products"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    plan: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    amount_minor: Mapped[int] = mapped_column(Integer)
    credits: Mapped[int] = mapped_column(Integer)
    billing_interval: Mapped[str] = mapped_column(String(20), default="one_time")
    provider_prices: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        CheckConstraint("amount_minor >= 0", name="ck_billing_products_amount_nonnegative"),
        CheckConstraint("credits > 0", name="ck_billing_products_credits_positive"),
        CheckConstraint(
            "billing_interval IN ('one_time', 'month', 'year')",
            name="ck_billing_products_interval",
        ),
    )


class BillingOrder(Base, TimestampMixin):
    """Order state owned by Moshu; provider callbacks only advance this state."""

    __tablename__ = "billing_orders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("billing_products.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    credits: Mapped[int] = mapped_column(Integer)
    product_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    provider_order_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    provider_checkout_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    failure_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    refunded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("uq_billing_orders_user_idempotency", "user_id", "idempotency_key", unique=True),
        Index("uq_billing_orders_provider_order", "provider", "provider_order_id", unique=True),
        CheckConstraint("amount_minor >= 0", name="ck_billing_orders_amount_nonnegative"),
        CheckConstraint("credits > 0", name="ck_billing_orders_credits_positive"),
        CheckConstraint(
            "status IN ('pending', 'paid', 'cancelled', 'failed', 'refunded', 'partially_refunded')",
            name="ck_billing_orders_status",
        ),
    )


class CreditGrant(Base):
    """Immutable source allocation for purchased, manual, and reversed credits."""

    __tablename__ = "credit_grants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("billing_orders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    credits: Mapped[int] = mapped_column(Integer)
    remaining_credits: Mapped[int] = mapped_column(Integer)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("credits > 0", name="ck_credit_grants_credits_positive"),
        CheckConstraint("remaining_credits >= 0 AND remaining_credits <= credits", name="ck_credit_grants_remaining"),
        CheckConstraint(
            "kind IN ('purchase', 'manual', 'refund', 'reversal')",
            name="ck_credit_grants_kind",
        ),
    )


class BillingWebhookEvent(Base):
    """Provider webhook inbox; provider event ids are globally idempotent per provider."""

    __tablename__ = "billing_webhook_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(20))
    provider_event_id: Mapped[str] = mapped_column(String(160))
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="received", index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("uq_billing_webhook_provider_event", "provider", "provider_event_id", unique=True),
        CheckConstraint("status IN ('received', 'processed', 'failed')", name="ck_billing_webhook_status"),
    )


class RatioReport(Base, TimestampMixin):
    """AI占比报告表"""

    __tablename__ = "ratio_reports"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    total_words: Mapped[int] = mapped_column(Integer)
    ai_raw_words: Mapped[int] = mapped_column(Integer)  # AI直出未改
    ai_edited_words: Mapped[int] = mapped_column(Integer)  # AI改过的
    human_words: Mapped[int] = mapped_column(Integer)  # 纯人写
    suspect_count: Mapped[int] = mapped_column(Integer)  # 疑似AI句式的段落数
    paragraphs: Mapped[list] = mapped_column(JSONB)  # 每段的来源标记
    suspects: Mapped[list] = mapped_column(JSONB)  # 疑似句式列表

    # 关系
    project: Mapped["Project"] = relationship()
