"""User-owned OpenAI-compatible generation configuration."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin


class UserModelConfig(Base, TimestampMixin):
    __tablename__ = "user_model_configs"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_model_configs_user_id"),
        CheckConstraint("revision > 0", name="ck_user_model_config_revision_positive"),
        CheckConstraint(
            "context_window_tokens BETWEEN 4096 AND 2000000",
            name="ck_user_model_config_context_window",
        ),
        CheckConstraint(
            "max_output_tokens BETWEEN 256 AND 131072",
            name="ck_user_model_config_max_output",
        ),
        CheckConstraint(
            "context_safety_margin_tokens BETWEEN 256 AND 262144",
            name="ck_user_model_config_context_margin",
        ),
        CheckConstraint(
            "context_window_tokens - max_output_tokens - "
            "context_safety_margin_tokens >= 1024",
            name="ck_user_model_config_input_budget",
        ),
        CheckConstraint(
            "last_test_status IN ('untested', 'ok', 'failed')",
            name="ck_user_model_config_test_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider_name: Mapped[str] = mapped_column(String(100))
    base_url: Mapped[str] = mapped_column(String(1000))
    model: Mapped[str] = mapped_column(String(200))
    context_window_tokens: Mapped[int] = mapped_column(
        Integer, default=32_768, server_default="32768"
    )
    max_output_tokens: Mapped[int] = mapped_column(
        Integer, default=4_096, server_default="4096"
    )
    context_safety_margin_tokens: Mapped[int] = mapped_column(
        Integer, default=2_048, server_default="2048"
    )
    api_key_ciphertext: Mapped[str] = mapped_column(Text)
    api_key_hint: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    last_test_status: Mapped[str] = mapped_column(
        String(20), default="untested", server_default="untested"
    )
    last_tested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
