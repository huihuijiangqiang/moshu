"""Models exposed by Volcengine Coding Plan's OpenAI-compatible route."""

from __future__ import annotations

VOLCENGINE_CODING_BASE_URL = "https://ark.cn-beijing.volces.com/api/coding/v3"

CODING_PLAN_MODELS: tuple[dict[str, object], ...] = (
    {"id": "auto", "name": "Auto", "contextWindow": 1_000_000, "tier": "auto"},
    {"id": "doubao-seed-evolving", "name": "Doubao-Seed-Evolving", "contextWindow": 1_000_000, "tier": "premium"},
    {"id": "doubao-seed-2.1-turbo", "name": "Doubao-Seed-2.1-turbo", "contextWindow": 256_000, "tier": "main"},
    {"id": "doubao-seed-2.0-lite", "name": "Doubao-Seed-2.0-lite", "contextWindow": 256_000, "tier": "cheap"},
    {"id": "glm-5.3-flash", "name": "GLM-5.3-Flash", "contextWindow": 256_000, "tier": "cheap"},
    {"id": "glm-5.3", "name": "GLM-5.3", "contextWindow": 1_000_000, "tier": "premium"},
    {"id": "deepseek-v4-pro", "name": "DeepSeek-V4-Pro", "contextWindow": 1_000_000, "tier": "premium"},
    {"id": "deepseek-v4-flash", "name": "DeepSeek-V4-Flash", "contextWindow": 1_000_000, "tier": "main"},
    {"id": "kimi-k3", "name": "Kimi-K3", "contextWindow": 1_000_000, "tier": "premium"},
    {"id": "kimi-k2.7-code", "name": "Kimi-K2.7-Code", "contextWindow": 1_000_000, "tier": "premium"},
    {"id": "minimax-m3", "name": "MiniMax-M3", "contextWindow": 256_000, "tier": "main"},
)


def get_model(model_id: str) -> dict[str, object] | None:
    return next((model for model in CODING_PLAN_MODELS if model["id"] == model_id), None)


__all__ = ["CODING_PLAN_MODELS", "VOLCENGINE_CODING_BASE_URL", "get_model"]
