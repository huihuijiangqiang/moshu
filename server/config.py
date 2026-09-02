"""
配置管理 - 从环境变量加载所有配置
"""
from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://moshu:moshu@localhost:5432/moshu"
    database_pool_size: int = 20
    database_max_overflow: int = 10

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # JWT
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30

    # Model Gateway
    model_gateway_cheap_url: str
    model_gateway_cheap_key: str
    model_gateway_main_url: str
    model_gateway_main_key: str
    model_gateway_premium_url: str
    model_gateway_premium_key: str

    # Object Storage
    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str
    s3_bucket_exports: str = "moshu-exports"
    s3_bucket_uploads: str = "moshu-uploads"

    # App
    debug: bool = False
    cors_origins: str = "http://localhost:5173"
    log_level: str = "INFO"

    # Consistency pipeline - 抽取/摘要/嵌入使用的网关与模型（不得在代码里硬编码）
    consistency_gateway_tier: str = "main"  # cheap, main, premium
    consistency_extraction_model: str = "gpt-4o-mini"
    consistency_summary_model: str = "gpt-4o-mini"
    consistency_arbitration_model: Optional[str] = None
    embedding_gateway_url: Optional[str] = None
    embedding_gateway_key: Optional[str] = None
    embedding_model: str = "doubao-embedding-vision"
    # 数据库列是 HALFVEC(2048)；换维度必须先做 schema migration。
    embedding_dimensions: int = 2048

    # 分块参数：长章节必须切块后全量处理，不能截断丢尾部
    consistency_chunk_chars: int = 6000
    consistency_chunk_overlap_chars: int = 400
    consistency_max_chunks: int = 40

    # SSE 流式与重试配置
    consistency_reasoning_effort: str = "low"  # none, low, medium, high
    consistency_request_timeout: float = 180.0  # 秒
    consistency_max_retries: int = 3
    consistency_retry_base_delay: float = 1.0  # 秒

    # Novel generation.  The model falls back to the configured consistency
    # summary model so deployments keep one source of truth unless overridden.
    generation_gateway_tier: str = "main"
    generation_model: Optional[str] = None
    generation_reasoning_effort: str = "low"
    generation_request_timeout: float = 600.0

    @field_validator("embedding_dimensions")
    @classmethod
    def validate_embedding_dimensions(cls, value: int) -> int:
        if value != 2048:
            raise ValueError(
                "embedding_dimensions must be 2048 for the HALFVEC(2048) schema"
            )
        return value

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",")]

    def gateway_url(self, tier: Optional[str] = None) -> str:
        """按 tier 取网关 URL。"""
        return self._gateway_pair(tier)[0]

    def gateway_key(self, tier: Optional[str] = None) -> str:
        """按 tier 取网关 key。"""
        return self._gateway_pair(tier)[1]

    @property
    def resolved_generation_model(self) -> str:
        return self.generation_model or self.consistency_summary_model

    @property
    def resolved_arbitration_model(self) -> str:
        return self.consistency_arbitration_model or self.consistency_extraction_model

    def _gateway_pair(self, tier: Optional[str]) -> tuple[str, str]:
        resolved = (tier or self.consistency_gateway_tier).lower()
        mapping = {
            "cheap": (self.model_gateway_cheap_url, self.model_gateway_cheap_key),
            "main": (self.model_gateway_main_url, self.model_gateway_main_key),
            "premium": (self.model_gateway_premium_url, self.model_gateway_premium_key),
        }
        if resolved not in mapping:
            raise ValueError(
                f"unknown model gateway tier: {resolved!r} (expected cheap, main or premium)"
            )
        return mapping[resolved]


settings = Settings()
