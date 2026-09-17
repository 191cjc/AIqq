"""Bound image size before publishing media for QQ to fetch."""

from __future__ import annotations

import logging
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from aiqq.logic.models import ImageAsset

from .validation import InvalidImage, validate_image


logger = logging.getLogger(__name__)
MAX_QQ_IMAGE_BYTES = 2 * 1024 * 1024
JPEG_QUALITIES = (90, 85, 80, 75, 70)
MAX_RESIZE_STEPS = 8
RESIZE_FACTOR = 0.75


def prepare_qq_image(image: ImageAsset) -> ImageAsset:
    """Keep small assets unchanged; compress oversized assets to bounded JPEGs."""
    if len(image.data) <= MAX_QQ_IMAGE_BYTES:
        return image

    validated = validate_image(image.data)
    try:
        with Image.open(BytesIO(validated.data)) as source:
            source.load()
            with ImageOps.exif_transpose(source) as oriented:
                if "A" in oriented.getbands() or "transparency" in oriented.info:
                    with oriented.convert("RGBA") as rgba:
                        rgb = Image.new("RGB", rgba.size, "white")
                        with rgba.getchannel("A") as alpha:
                            rgb.paste(rgba, mask=alpha)
                else:
                    rgb = oriented.convert("RGB")

        # Discard source metadata, including orientation already applied above.
        rgb.info.clear()
        try:
            for resize_step in range(MAX_RESIZE_STEPS + 1):
                for quality in JPEG_QUALITIES:
                    output = BytesIO()
                    rgb.save(output, format="JPEG", quality=quality, optimize=True)
                    data = output.getvalue()
                    if len(data) <= MAX_QQ_IMAGE_BYTES:
                        prepared = validate_image(data)
                        logger.info(
                            "event=qq_image_compressed input_bytes=%s output_bytes=%s "
                            "input_width=%s input_height=%s output_width=%s "
                            "output_height=%s quality=%s",
                            len(image.data), len(prepared.data),
                            validated.width, validated.height,
                            prepared.width, prepared.height, quality,
                        )
                        return prepared
                if resize_step == MAX_RESIZE_STEPS:
                    break
                resized = rgb.resize(
                    (
                        max(1, int(rgb.width * RESIZE_FACTOR)),
                        max(1, int(rgb.height * RESIZE_FACTOR)),
                    ),
                    Image.Resampling.LANCZOS,
                )
                rgb.close()
                rgb = resized
        finally:
            rgb.close()
    except InvalidImage:
        raise
    except (
        Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError
    ) as exc:
        raise InvalidImage("image cannot be prepared for QQ upload") from exc
    raise InvalidImage("compressed image exceeds the QQ upload size limit")
