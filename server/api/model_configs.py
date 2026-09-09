"""Self-service encrypted model configuration for authenticated users."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from config import (
    DEFAULT_CONTEXT_SAFETY_MARGIN_TOKENS,
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    MIN_CONTEXT_INPUT_TOKENS,
)
from db.models_core import User
from db.models_model_config import UserModelConfig
from db.session import get_db
from services.model_configs import (
    InvalidModelEndpointError,
    ModelConfigProbe,
    api_key_hint,
    encrypt_api_key,
    new_config_id,
    normalize_public_base_url,
)

router = APIRouter()


class ModelConfigWrite(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider_name: str = Field(alias="providerName", min_length=1, max_length=100)
    base_url: str = Field(alias="baseUrl", min_length=1, max_length=1000)
    model: str = Field(min_length=1, max_length=200)
    context_window_tokens: int = Field(
        default=DEFAULT_CONTEXT_WINDOW_TOKENS,
        alias="contextWindowTokens",
        strict=True,
        ge=4_096,
        le=2_000_000,
    )
    max_output_tokens: int = Field(
        default=DEFAULT_MAX_OUTPUT_TOKENS,
        alias="maxOutputTokens",
        strict=True,
        ge=256,
        le=131_072,
    )
    context_safety_margin_tokens: int = Field(
        default=DEFAULT_CONTEXT_SAFETY_MARGIN_TOKENS,
        alias="contextSafetyMarginTokens",
        strict=True,
        ge=256,
        le=262_144,
    )
    api_key: str | None = Field(default=None, alias="apiKey", min_length=8, max_length=512)
    enabled: bool = True
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_context_budget(self) -> "ModelConfigWrite":
        available_input = (
            self.context_window_tokens
            - self.max_output_tokens
            - self.context_safety_margin_tokens
        )
        if available_input < MIN_CONTEXT_INPUT_TOKENS:
            raise ValueError(
                "context budget must reserve at least "
                f"{MIN_CONTEXT_INPUT_TOKENS} tokens for input"
            )
        return self


class ModelConfigTest(BaseModel):
    revision: int = Field(ge=1)


def get_model_config_probe() -> ModelConfigProbe:
    return ModelConfigProbe()


def _payload(config: UserModelConfig | None) -> dict:
    if config is None:
        return {
            "configured": False,
            "providerName": "",
            "baseUrl": "",
            "model": "",
            "contextWindowTokens": DEFAULT_CONTEXT_WINDOW_TOKENS,
            "maxOutputTokens": DEFAULT_MAX_OUTPUT_TOKENS,
            "contextSafetyMarginTokens": DEFAULT_CONTEXT_SAFETY_MARGIN_TOKENS,
            "keyHint": None,
            "enabled": False,
            "revision": 0,
            "lastTestStatus": "untested",
            "lastTestedAt": None,
            "lastErrorCode": None,
        }
    return {
        "configured": True,
        "providerName": config.provider_name,
        "baseUrl": config.base_url,
        "model": config.model,
        "contextWindowTokens": config.context_window_tokens,
        "maxOutputTokens": config.max_output_tokens,
        "contextSafetyMarginTokens": config.context_safety_margin_tokens,
        "keyHint": config.api_key_hint,
        "enabled": config.enabled,
        "revision": config.revision,
        "lastTestStatus": config.last_test_status,
        "lastTestedAt": config.last_tested_at.isoformat() if config.last_tested_at else None,
        "lastErrorCode": config.last_error_code,
    }


async def _owned_config(db: AsyncSession, user_id: str, *, lock: bool = False) -> UserModelConfig | None:
    query = select(UserModelConfig).where(UserModelConfig.user_id == user_id)
    if lock:
        query = query.with_for_update()
    return await db.scalar(query)


def _conflict(config: UserModelConfig) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"code": "MODEL_CONFIG_REVISION_CONFLICT", "currentRevision": config.revision},
    )


@router.get("/model-config")
async def get_model_config(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return _payload(await _owned_config(db, user.id))


@router.put("/model-config")
async def save_model_config(
    request: ModelConfigWrite,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    provider_name = request.provider_name.strip()
    model = request.model.strip()
    if not provider_name or not model:
        raise HTTPException(status_code=422, detail={"code": "MODEL_CONFIG_FIELDS_REQUIRED"})
    try:
        base_url = normalize_public_base_url(request.base_url)
    except InvalidModelEndpointError as exc:
        raise HTTPException(status_code=422, detail={"code": "MODEL_ENDPOINT_NOT_ALLOWED"}) from exc

    # Lock the owner as the stable serialization point even before a config
    # row exists, so two first-save requests cannot race the unique constraint.
    await db.scalar(select(User).where(User.id == user.id).with_for_update())
    config = await _owned_config(db, user.id, lock=True)
    if config is None:
        if request.revision != 0:
            raise HTTPException(status_code=409, detail={"code": "MODEL_CONFIG_MISSING"})
        if request.api_key is None:
            raise HTTPException(status_code=422, detail={"code": "MODEL_API_KEY_REQUIRED"})
        config_id = new_config_id()
        try:
            ciphertext = encrypt_api_key(request.api_key, user_id=user.id, config_id=config_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": "MODEL_API_KEY_INVALID"}) from exc
        config = UserModelConfig(
            id=config_id,
            user_id=user.id,
            provider_name=provider_name,
            base_url=base_url,
            model=model,
            context_window_tokens=request.context_window_tokens,
            max_output_tokens=request.max_output_tokens,
            context_safety_margin_tokens=request.context_safety_margin_tokens,
            api_key_ciphertext=ciphertext,
            api_key_hint=api_key_hint(request.api_key),
            enabled=request.enabled,
            revision=1,
            last_test_status="untested",
        )
        db.add(config)
    else:
        if request.revision != config.revision:
            raise _conflict(config)
        config.provider_name = provider_name
        config.base_url = base_url
        config.model = model
        config.context_window_tokens = request.context_window_tokens
        config.max_output_tokens = request.max_output_tokens
        config.context_safety_margin_tokens = request.context_safety_margin_tokens
        config.enabled = request.enabled
        if request.api_key is not None:
            try:
                config.api_key_ciphertext = encrypt_api_key(
                    request.api_key, user_id=user.id, config_id=config.id
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail={"code": "MODEL_API_KEY_INVALID"}) from exc
            config.api_key_hint = api_key_hint(request.api_key)
        config.revision += 1
        config.last_test_status = "untested"
        config.last_tested_at = None
        config.last_error_code = None
    await db.commit()
    await db.refresh(config)
    return _payload(config)


@router.post("/model-config/test")
async def test_model_config(
    request: ModelConfigTest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    probe: ModelConfigProbe = Depends(get_model_config_probe),
):
    config = await _owned_config(db, user.id)
    if config is None:
        raise HTTPException(status_code=404, detail={"code": "MODEL_CONFIG_NOT_FOUND"})
    if request.revision != config.revision:
        raise _conflict(config)

    result = await probe.test(config)
    current = await _owned_config(db, user.id, lock=True)
    if current is None:
        raise HTTPException(status_code=404, detail={"code": "MODEL_CONFIG_NOT_FOUND"})
    if current.revision != request.revision:
        raise _conflict(current)
    current.last_test_status = "ok" if result.ok else "failed"
    current.last_error_code = None if result.ok else result.code
    current.last_tested_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(current)
    return _payload(current)


@router.delete("/model-config", status_code=204)
async def delete_model_config(
    revision: int = Query(ge=1),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    config = await _owned_config(db, user.id, lock=True)
    if config is None:
        return Response(status_code=204)
    if revision != config.revision:
        raise _conflict(config)
    await db.delete(config)
    await db.commit()
    return Response(status_code=204)
