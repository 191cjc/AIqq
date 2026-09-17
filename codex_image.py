import base64
import binascii
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, UnidentifiedImageError


CODEX_IMAGE_MAX_BYTES = 20 * 1024 * 1024
CODEX_IMAGE_MAX_BASE64_CHARS = ((CODEX_IMAGE_MAX_BYTES + 2) // 3) * 4
CODEX_IMAGE_MAX_PIXELS = 4096 * 4096
CODEX_IMAGE_MIME_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


class CodexImageError(ValueError):
    pass


@dataclass(frozen=True)
class CodexGeneratedImage:
    data: bytes
    mime_type: str


def decode_codex_image(encoded: str) -> CodexGeneratedImage:
    value = encoded.strip()
    if value.startswith("data:"):
        header, separator, value = value.partition(",")
        if not separator or ";base64" not in header.lower():
            raise CodexImageError("Codex 返回了无效的图片数据 URI。")
    if not value or len(value) > CODEX_IMAGE_MAX_BASE64_CHARS:
        raise CodexImageError("Codex 返回的图片大小不合法。")
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CodexImageError("Codex 返回了无效的 Base64 图片数据。") from exc
    if not data or len(data) > CODEX_IMAGE_MAX_BYTES:
        raise CodexImageError("Codex 返回的图片大小不合法。")

    return validate_codex_image(data)


def validate_codex_image(data: bytes) -> CodexGeneratedImage:
    if not data or len(data) > CODEX_IMAGE_MAX_BYTES:
        raise CodexImageError("Codex 返回的图片大小不合法。")

    try:
        with Image.open(BytesIO(data)) as source:
            width, height = source.size
            image_format = (source.format or "").upper()
            if (
                width < 1
                or height < 1
                or width * height > CODEX_IMAGE_MAX_PIXELS
            ):
                raise CodexImageError("Codex 返回的图片尺寸不合法。")
            mime_type = CODEX_IMAGE_MIME_TYPES.get(image_format)
            if mime_type is None:
                raise CodexImageError("Codex 返回了不支持的图片格式。")
            source.verify()
    except CodexImageError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise CodexImageError("Codex 返回的图片无法解析。") from exc

    return CodexGeneratedImage(data, mime_type)
