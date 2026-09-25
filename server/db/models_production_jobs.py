"""Durable, billed image-generation requests for comic-drama shots."""

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin


class ProductionJob(Base, TimestampMixin):
    __tablename__ = "production_jobs"
    __table_args__ = (
        Index("ix_production_jobs_adaptation_shot", "adaptation_id", "shot_id"),
        Index("ix_production_jobs_visual_profile_id", "visual_profile_id"),
        Index("ix_production_jobs_status_created", "status", "created_at"),
        Index("uq_production_jobs_user_request", "user_id", "client_request_id", unique=True),
        CheckConstraint("credits > 0", name="ck_production_jobs_credits_positive"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    adaptation_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("adaptations.id", ondelete="CASCADE"), nullable=False
    )
    episode_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("adaptation_episodes.id", ondelete="SET NULL"), nullable=True
    )
    shot_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("adaptation_shots.id", ondelete="SET NULL"), nullable=True
    )
    visual_profile_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("adaptation_visual_profiles.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    client_request_id: Mapped[str] = mapped_column(String(100), nullable=False)
    usage_log_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("usage_logs.id", ondelete="SET NULL"), nullable=True
    )
    asset_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("production_assets.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_versions: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
