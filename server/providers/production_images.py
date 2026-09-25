"""Bounded OpenAI-compatible image adapter for one storyboard frame."""

import asyncio
import base64
import binascii
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

from config import Settings, settings
from services.production_assets import InvalidProductionImageError, normalize_generated_image


class ImageGatewayError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    mime_type: str
    width: int
    height: int
    provider_id: str | None


def image_gateway_config(config: Settings = settings) -> tuple[str, str]:
    raw_url = (config.image_gateway_url or config.model_gateway_main_url).strip().rstrip("/")
    key = (config.image_gateway_key or config.model_gateway_main_key).strip()
    parts = urlsplit(raw_url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or not key:
        raise ImageGatewayError("image_gateway_not_configured")
    path = parts.path.rstrip("/")
    if path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")] + "/images/generations"
    elif path.endswith("/responses"):
        path = path[: -len("/responses")] + "/images/generations"
    elif path.endswith("/images/generations"):
        pass
    elif not path:
        path = "/v1/images/generations"
    elif path.endswith("/v1"):
        path += "/images/generations"
    else:
        raise ImageGatewayError("image_gateway_not_configured")
    return urlunsplit((parts.scheme, parts.netloc, path, "", "")), key


async def generate_storyboard_image(
    prompt: str,
    *,
    aspect_ratio: str,
    model: str | None = None,
    config: Settings = settings,
    client: httpx.AsyncClient | None = None,
) -> GeneratedImage:
    endpoint, key = image_gateway_config(config)
    size = "1024x1536" if aspect_ratio == "9:16" else "1536x1024"
    payload = {"model": model or config.image_gateway_model, "prompt": prompt, "size": size}
    owned_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0), follow_redirects=False)
    try:
        try:
            async with client.stream("POST", endpoint, headers={"Authorization": f"Bearer {key}"}, json=payload) as response:
                if response.status_code in (401, 403):
                    raise ImageGatewayError("image_provider_forbidden")
                if response.status_code == 429:
                    raise ImageGatewayError("image_provider_rate_limited")
                if response.status_code >= 500:
                    raise ImageGatewayError("image_provider_unavailable")
                if response.status_code != 200:
                    raise ImageGatewayError("image_provider_rejected")
                max_encoded = ((config.production_asset_max_bytes + 2) // 3) * 4
                max_response = max_encoded + 100_000
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(chunks) + len(chunk) > max_response:
                        raise ImageGatewayError("image_response_too_large")
                    chunks.extend(chunk)
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise ImageGatewayError("image_provider_unavailable") from error
        try:
            body = httpx.Response(200, content=bytes(chunks)).json()
            item = body["data"][0]
            encoded = item["b64_json"]
            if not isinstance(encoded, str) or len(encoded) > max_encoded:
                raise ValueError("missing or oversized base64 image")
            raw = base64.b64decode(encoded, validate=True)
        except (KeyError, IndexError, TypeError, ValueError, binascii.Error) as error:
            raise ImageGatewayError("image_response_invalid") from error
        if len(raw) > config.production_asset_max_bytes:
            raise ImageGatewayError("image_response_too_large")
        try:
            data, mime_type, width, height = await asyncio.to_thread(normalize_generated_image, raw)
        except InvalidProductionImageError as error:
            raise ImageGatewayError("image_response_invalid") from error
        provider_id = body.get("id")
        return GeneratedImage(
            data=data, mime_type=mime_type, width=width, height=height,
            provider_id=provider_id[:100] if isinstance(provider_id, str) else None,
        )
    finally:
        if owned_client:
            await client.aclose()
