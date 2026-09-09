"""Encryption, validation, and probing for user-owned model credentials."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import os
import socket
import uuid
from dataclasses import dataclass, field
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models_model_config import UserModelConfig


class InvalidModelEndpointError(ValueError):
    pass


class CredentialDecryptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class UserGenerationRoute:
    config_id: str
    endpoint: str
    model: str
    api_key: str = field(repr=False)
    context_window_tokens: int = 32_768
    max_output_tokens: int = 4_096
    context_safety_margin_tokens: int = 2_048


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    code: str


def _encryption_key() -> bytes:
    source = settings.credential_encryption_key or settings.jwt_secret_key
    return hashlib.sha256(f"moshu:user-model-credential:v1:{source}".encode()).digest()


def _aad(user_id: str, config_id: str) -> bytes:
    return f"user-model-config:{user_id}:{config_id}".encode()


def encrypt_api_key(value: str, *, user_id: str, config_id: str) -> str:
    secret = value.strip()
    if len(secret) < 8 or len(secret) > 512 or any(character.isspace() for character in secret):
        raise ValueError("invalid API key")
    nonce = os.urandom(12)
    ciphertext = AESGCM(_encryption_key()).encrypt(nonce, secret.encode(), _aad(user_id, config_id))
    return "v1." + base64.urlsafe_b64encode(nonce + ciphertext).decode()


def decrypt_api_key(value: str, *, user_id: str, config_id: str) -> str:
    try:
        version, encoded = value.split(".", 1)
        if version != "v1":
            raise ValueError("unsupported credential version")
        payload = base64.urlsafe_b64decode(encoded.encode())
        plaintext = AESGCM(_encryption_key()).decrypt(payload[:12], payload[12:], _aad(user_id, config_id))
        return plaintext.decode()
    except Exception as exc:
        raise CredentialDecryptionError("model credential cannot be decrypted") from exc


def api_key_hint(value: str) -> str:
    secret = value.strip()
    prefix = secret[:3] if len(secret) >= 3 else "key"
    return f"{prefix}****{secret[-4:]}"


def normalize_public_base_url(value: str) -> str:
    raw = value.strip()
    if len(raw) > 1000:
        raise InvalidModelEndpointError("endpoint is too long")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise InvalidModelEndpointError("only public HTTPS endpoints are allowed")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise InvalidModelEndpointError("endpoint credentials, query, and fragment are not allowed")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise InvalidModelEndpointError("local endpoints are not allowed")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise InvalidModelEndpointError("private or reserved endpoints are not allowed")

    try:
        port = parsed.port
    except ValueError as exc:
        raise InvalidModelEndpointError("invalid endpoint port") from exc
    netloc = f"[{hostname}]" if address and address.version == 6 else hostname
    if port is not None:
        netloc += f":{port}"
    path = "/" + parsed.path.strip("/") if parsed.path.strip("/") else ""
    return urlunsplit(SplitResult("https", netloc, path, "", ""))


def chat_completions_url(base_url: str) -> str:
    normalized = normalize_public_base_url(base_url)
    if normalized.rstrip("/").endswith("/chat/completions"):
        return normalized
    return f"{normalized.rstrip('/')}/chat/completions"


def models_url(base_url: str) -> str:
    normalized = normalize_public_base_url(base_url).rstrip("/")
    suffix = "/chat/completions"
    if normalized.endswith(suffix):
        normalized = normalized[: -len(suffix)]
    return f"{normalized}/models"


async def assert_public_endpoint_resolution(value: str) -> None:
    """Reject hostnames that currently resolve to non-public networks."""
    parsed = urlsplit(normalize_public_base_url(value))
    hostname = parsed.hostname
    if hostname is None:
        raise InvalidModelEndpointError("endpoint hostname is missing")
    try:
        answers = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            parsed.port or 443,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise InvalidModelEndpointError("endpoint hostname cannot be resolved") from exc
    addresses = {answer[4][0] for answer in answers}
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise InvalidModelEndpointError("endpoint resolves to a private or reserved address")


async def active_user_generation_route(
    db: AsyncSession,
    *,
    user_id: str,
) -> UserGenerationRoute | None:
    config = await db.scalar(
        select(UserModelConfig).where(
            UserModelConfig.user_id == user_id,
            UserModelConfig.enabled,
        )
    )
    if config is None:
        return None
    return UserGenerationRoute(
        config_id=config.id,
        endpoint=chat_completions_url(config.base_url),
        model=config.model,
        api_key=decrypt_api_key(config.api_key_ciphertext, user_id=user_id, config_id=config.id),
        context_window_tokens=config.context_window_tokens,
        max_output_tokens=config.max_output_tokens,
        context_safety_margin_tokens=config.context_safety_margin_tokens,
    )


class ModelConfigProbe:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    async def test(self, config: UserModelConfig) -> ProbeResult:
        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=8.0),
            follow_redirects=False,
        )
        try:
            await assert_public_endpoint_resolution(config.base_url)
            response = await client.get(
                models_url(config.base_url),
                headers={
                    "Authorization": "Bearer "
                    + decrypt_api_key(
                        config.api_key_ciphertext,
                        user_id=config.user_id,
                        config_id=config.id,
                    ),
                    "Accept": "application/json",
                },
            )
            if response.status_code in {401, 403}:
                return ProbeResult(False, "authentication_failed")
            if response.status_code == 404:
                return ProbeResult(False, "models_endpoint_not_found")
            if response.is_error:
                return ProbeResult(False, "provider_error")
            return ProbeResult(True, "ok")
        except InvalidModelEndpointError:
            return ProbeResult(False, "endpoint_not_allowed")
        except (httpx.TimeoutException, httpx.TransportError):
            return ProbeResult(False, "connection_failed")
        finally:
            if self._client is None:
                await client.aclose()


def new_config_id() -> str:
    return uuid.uuid4().hex
