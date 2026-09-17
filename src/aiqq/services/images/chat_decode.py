"""Bounded input-only decoding for chat vision, including animated stickers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
from typing import Any
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from aiqq.logic.models import ImageAsset

from .validation import MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS


MAX_ANIMATION_FRAMES = 120
MAX_ANIMATION_PIXELS = 64 * 1024 * 1024
MAX_READ_FRAMES = 3
READ_MIME_TYPES = {
    "JPEG": "image/jpeg", "PNG": "image/png",
    "WEBP": "image/webp", "GIF": "image/gif",
}


class ChatImageDecodeError(ValueError):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class DecodedChatImage:
    images: tuple[ImageAsset, ...]
    metadata: dict[str, Any]


def decode_chat_image(data: bytes) -> DecodedChatImage:
    """Inspect a bounded animation and return at most first/middle/last JPEGs.

    This boundary intentionally differs from generated-image validation: GIF
    inputs become ordinary static visual inputs, never a generated GIF asset.
    """
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ChatImageDecodeError("too_large" if data else "invalid_image")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as source:
                mime_type = READ_MIME_TYPES.get((source.format or "").upper())
                if mime_type is None:
                    raise ChatImageDecodeError("invalid_image")
                width, height = source.size
                if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
                    raise ChatImageDecodeError("too_large")
                count = 0
                durations: list[int] = []
                pixels = 0
                # Avoid n_frames, which can traverse an unbounded GIF stream.
                while True:
                    try:
                        source.seek(count)
                    except EOFError:
                        break
                    if count >= MAX_ANIMATION_FRAMES:
                        raise ChatImageDecodeError("too_large")
                    pixels += source.width * source.height
                    if source.width * source.height > MAX_IMAGE_PIXELS or pixels > MAX_ANIMATION_PIXELS:
                        raise ChatImageDecodeError("too_large")
                    source.load()
                    duration = source.info.get("duration", 0)
                    durations.append(max(0, int(duration)) if isinstance(duration, (int, float)) else 0)
                    count += 1
                if not count:
                    raise ChatImageDecodeError("invalid_image")
                indices = sorted({0, count // 2, count - 1}) if count > 1 else [0]
                frames: list[ImageAsset] = []
                frame_metadata: list[dict[str, Any]] = []
                for index in indices:
                    source.seek(index)
                    frame = ImageOps.exif_transpose(source.copy()).convert("RGBA")
                    rgb = Image.new("RGB", frame.size, "white")
                    rgb.paste(frame, mask=frame.getchannel("A"))
                    output = BytesIO()
                    rgb.save(output, format="JPEG", quality=90)
                    frame_bytes = output.getvalue()
                    if len(frame_bytes) > MAX_IMAGE_BYTES:
                        raise ChatImageDecodeError("too_large")
                    frames.append(ImageAsset(frame_bytes, "image/jpeg", rgb.width, rgb.height))
                    frame_metadata.append({
                        "frame_index": index,
                        "offset_ms": sum(durations[:index]),
                        "duration_ms": durations[index],
                        "width": rgb.width, "height": rgb.height,
                    })
                return DecodedChatImage(tuple(frames), {
                    "mime_type": mime_type, "width": width, "height": height,
                    "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                    "frame_count": count, "animated": count > 1,
                    "sampled": count > len(frames), "frames": frame_metadata,
                    "measurement_source": "on_demand_download",
                })
    except ChatImageDecodeError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ChatImageDecodeError("too_large") from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, OverflowError):
        raise ChatImageDecodeError("invalid_image") from None
