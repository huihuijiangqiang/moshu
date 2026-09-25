"""Private visual assets produced for a comic-drama adaptation."""

from typing import Optional

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin


class ProductionAsset(Base, TimestampMixin):
    """One immutable upload/result that can be reviewed and referenced by shots."""

    __tablename__ = "production_assets"
    __table_args__ = (
        Index("ix_production_assets_adaptation_id", "adaptation_id"),
        Index("ix_production_assets_episode_id", "episode_id"),
        Index("ix_production_assets_shot_id", "shot_id"),
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
    kind: Mapped[str] = mapped_column(String(30), default="image")
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)
    mime_type: Mapped[str] = mapped_column(String(100))
    byte_size: Mapped[int] = mapped_column(Integer)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_by: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="RESTRICT"))
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
