"""
风格、用量、占比模型 - 4张表
"""
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, func, text
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

    # 关系
    user: Mapped["User"] = relationship()


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
