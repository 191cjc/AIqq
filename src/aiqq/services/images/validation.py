"""Validation boundary for images received from external services."""

from __future__ import annotations

import base64
import binascii
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from aiqq.logic.models import ImageAsset


MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_BASE64_CHARS = ((MAX_IMAGE_BYTES + 2) // 3) * 4
MAX_IMAGE_PIXELS = 4096 * 4096
IMAGE_MIME_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


class InvalidImage(ValueError):
    """Raised when bytes cannot cross the trusted image boundary."""


def decode_base64_image(encoded: str) -> ImageAsset:
    value = encoded.strip()
    if value.startswith("data:"):
        header, separator, value = value.partition(",")
        if not separator or ";base64" not in header.lower():
            raise InvalidImage("invalid image data URI")
    if not value or len(value) > MAX_IMAGE_BASE64_CHARS:
        raise InvalidImage("image payload size is invalid")
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidImage("image payload is not valid Base64") from exc
    return validate_image(data)


def validate_image(data: bytes) -> ImageAsset:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise InvalidImage("image payload size is invalid")
    try:
        with Image.open(BytesIO(data)) as source:
            width, height = source.size
            image_format = (source.format or "").upper()
            if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
                raise InvalidImage("image dimensions are invalid")
            mime_type = IMAGE_MIME_TYPES.get(image_format)
            if mime_type is None:
                raise InvalidImage("image format is unsupported")
            source.verify()
    except InvalidImage:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImage("image payload cannot be decoded") from exc
    return ImageAsset(data=data, mime_type=mime_type, width=width, height=height)
