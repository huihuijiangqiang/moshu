"""Shared validation and private paths for comic-drama image assets."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from config import settings

IMAGE_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_IMAGE_FORMATS = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}


class InvalidProductionImageError(ValueError):
    pass


def asset_root() -> Path:
    root = Path(settings.production_asset_dir).expanduser()
    return root if root.is_absolute() else Path(__file__).resolve().parents[1] / root


def asset_path(storage_key: str) -> Path:
    root = asset_root().resolve()
    candidate = (root / storage_key).resolve()
    if candidate == root or root not in candidate.parents:
        raise InvalidProductionImageError("invalid storage key")
    return candidate


def normalize_image(data: bytes, mime_type: str) -> tuple[bytes, int, int]:
    if mime_type not in _IMAGE_FORMATS:
        raise InvalidProductionImageError("unsupported image type")
    try:
        with Image.open(BytesIO(data)) as source:
            if source.format != _IMAGE_FORMATS[mime_type] or getattr(source, "n_frames", 1) != 1:
                raise InvalidProductionImageError("unexpected image format or animation")
            width, height = source.size
            if width < 1 or height < 1 or width > 8192 or height > 8192 or width * height > 16_000_000:
                raise InvalidProductionImageError("image dimensions out of bounds")
            source.load()
            oriented = ImageOps.exif_transpose(source)
            image = oriented.convert("RGB" if mime_type == "image/jpeg" else "RGBA")
            output = BytesIO()
            options = {"quality": 90} if mime_type == "image/jpeg" else {}
            image.save(output, format=_IMAGE_FORMATS[mime_type], **options)
            return output.getvalue(), image.width, image.height
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
        raise InvalidProductionImageError("invalid image data") from error


def normalize_generated_image(data: bytes) -> tuple[bytes, str, int, int]:
    try:
        with Image.open(BytesIO(data)) as source:
            mime_type = next((mime for mime, image_format in _IMAGE_FORMATS.items() if image_format == source.format), None)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise InvalidProductionImageError("invalid generated image") from error
    if mime_type is None:
        raise InvalidProductionImageError("unsupported generated image type")
    normalized, width, height = normalize_image(data, mime_type)
    if len(normalized) > settings.production_asset_max_bytes:
        raise InvalidProductionImageError("generated image too large")
    return normalized, mime_type, width, height
