import base64
from io import BytesIO

import httpx
import pytest
from PIL import Image, PngImagePlugin

from config import settings
from providers.production_images import ImageGatewayError, generate_storyboard_image, image_gateway_config
from services.production_assets import normalize_generated_image


def png_with_metadata() -> bytes:
    output = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private_note", "DO_NOT_EXPORT_THIS_METADATA")
    Image.new("RGB", (64, 96), "#ad5363").save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()


def test_generated_image_is_normalized_without_metadata():
    normalized, mime_type, width, height = normalize_generated_image(png_with_metadata())
    assert (mime_type, width, height) == ("image/png", 64, 96)
    assert b"DO_NOT_EXPORT_THIS_METADATA" not in normalized


def test_image_gateway_rejects_non_https_or_unexpected_routes(monkeypatch):
    monkeypatch.setattr(settings, "image_gateway_url", "http://localhost:8000/v1")
    monkeypatch.setattr(settings, "image_gateway_key", "test-key")
    with pytest.raises(ImageGatewayError, match="image_gateway_not_configured"):
        image_gateway_config()
    monkeypatch.setattr(settings, "image_gateway_url", "https://image.example/private/redirect")
    with pytest.raises(ImageGatewayError, match="image_gateway_not_configured"):
        image_gateway_config()


@pytest.mark.asyncio
async def test_image_adapter_uses_bounded_base64_response(monkeypatch):
    monkeypatch.setattr(settings, "image_gateway_url", "https://image.example/v1")
    monkeypatch.setattr(settings, "image_gateway_key", "test-key")
    encoded = base64.b64encode(png_with_metadata()).decode("ascii")
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "provider-result", "data": [{"b64_json": encoded}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        image = await generate_storyboard_image("locked character profile", aspect_ratio="9:16", client=client)

    assert requests[0].url == "https://image.example/v1/images/generations"
    assert requests[0].headers["Authorization"] == "Bearer test-key"
    assert requests[0].read().decode().count('"gpt-image-2"') == 1
    assert b'"size":"1024x1536"' in requests[0].read()
    assert (image.width, image.height, image.provider_id) == (64, 96, "provider-result")
    assert b"DO_NOT_EXPORT_THIS_METADATA" not in image.data


@pytest.mark.asyncio
async def test_image_adapter_does_not_fetch_provider_urls(monkeypatch):
    monkeypatch.setattr(settings, "image_gateway_url", "https://image.example/v1/images/generations")
    monkeypatch.setattr(settings, "image_gateway_key", "test-key")
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"data": [{"url": "https://untrusted.example/asset"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ImageGatewayError, match="image_response_invalid"):
            await generate_storyboard_image("frame", aspect_ratio="16:9", client=client)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_image_adapter_rejects_oversized_response_while_streaming(monkeypatch):
    monkeypatch.setattr(settings, "image_gateway_url", "https://image.example/v1")
    monkeypatch.setattr(settings, "image_gateway_key", "test-key")
    monkeypatch.setattr(settings, "production_asset_max_bytes", 32)

    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"x" * 100_100)
    )) as client:
        with pytest.raises(ImageGatewayError, match="image_response_too_large"):
            await generate_storyboard_image("frame", aspect_ratio="9:16", client=client)


@pytest.mark.asyncio
async def test_image_adapter_reports_permission_failure_without_upstream_details(monkeypatch):
    monkeypatch.setattr(settings, "image_gateway_url", "https://image.example/v1")
    monkeypatch.setattr(settings, "image_gateway_key", "test-key")
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(403, json={"error": {"message": "private provider details"}})
    )) as client:
        with pytest.raises(ImageGatewayError, match="image_provider_forbidden") as caught:
            await generate_storyboard_image("frame", aspect_ratio="9:16", client=client)
    assert "private provider details" not in str(caught.value)
