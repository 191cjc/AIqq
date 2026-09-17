import asyncio
import base64
import binascii
import json
import logging
import os
import re
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import aiohttp
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
)
from PIL import Image, ImageOps, UnidentifiedImageError

from ai_service import DEFAULT_BASE_URL, env_int


logger = logging.getLogger(__name__)
DEFAULT_GPT_IMAGE_MODEL = "gpt-image-2"
GPT_IMAGE_SIZE = "1024x1024"
GPT_IMAGE_QUALITY = "medium"
GPT_IMAGE_OUTPUT_FORMAT = "jpeg"
GPT_IMAGE_OUTPUT_COMPRESSION = 85
GPT_IMAGE_TARGET_SIZE = (1024, 1024)
GPT_IMAGE_MIME_TYPE = "image/jpeg"
GPT_IMAGE_MAX_BYTES = 20 * 1024 * 1024
GPT_IMAGE_MAX_SOURCE_PIXELS = 4096 * 4096
GPT_IMAGE_INPUT_FIDELITY = "high"
REFERENCE_IMAGE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
SAFE_IMAGE_INSTRUCTION = (
    "Create a safe-for-work image. Do not include nudity, sexual content, "
    "fetish content, or sexualized minors. User request: "
)
ERROR_DETAIL_KEYS = ("message", "type", "code", "param")
ERROR_DETAIL_MAX_CHARS = 1000


class GPTImageError(RuntimeError):
    pass


@dataclass(frozen=True)
class GPTGeneratedImage:
    data: bytes
    mime_type: str


def _sanitize_error_text(value: str) -> str:
    text = re.sub(r"(?i)Bearer\s+\S+", "Bearer <redacted>", value)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+\b", "<redacted>", text)
    return re.sub(r"\s+", " ", text).strip()[:ERROR_DETAIL_MAX_CHARS]


def _status_error_detail(error: APIStatusError) -> str:
    body = error.body
    if isinstance(body, dict):
        nested = body.get("error")
        if isinstance(nested, dict):
            body = nested
        selected = {
            key: body[key]
            for key in ERROR_DETAIL_KEYS
            if key in body and body[key] is not None
        }
        if selected:
            return _sanitize_error_text(
                json.dumps(selected, ensure_ascii=False, separators=(",", ":"))
            )
    elif isinstance(body, str) and body.strip():
        return _sanitize_error_text(body)

    response = getattr(error, "response", None)
    response_text = getattr(response, "text", "")
    return _sanitize_error_text(response_text) if response_text else "unknown"


def _status_error_request_id(error: APIStatusError) -> str:
    request_id = getattr(error, "request_id", None)
    if isinstance(request_id, str) and request_id:
        return request_id
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {})
    return headers.get("x-request-id") or headers.get("request-id") or "unknown"


class GPTImageService:
    def __init__(
        self,
        client: Any | None,
        *,
        model: str = DEFAULT_GPT_IMAGE_MODEL,
        download_timeout_seconds: int = 60,
    ):
        self._client = client
        self.model = model
        self._download_timeout_seconds = download_timeout_seconds

    @classmethod
    def from_env(cls) -> "GPTImageService":
        api_key = (
            os.getenv("OPENAI_IMAGE_API_KEY", "").strip()
            or os.getenv("OPENAI_API_KEY", "").strip()
        )
        base_url = (
            os.getenv("OPENAI_IMAGE_BASE_URL", "").strip()
            or os.getenv("OPENAI_BASE_URL", DEFAULT_BASE_URL).strip()
        )
        timeout_seconds = env_int(
            "OPENAI_IMAGE_TIMEOUT_SECONDS", 240, 30, 600
        )
        client = None
        if api_key:
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=float(timeout_seconds),
                max_retries=2,
            )
        return cls(
            client,
            model=os.getenv(
                "OPENAI_IMAGE_MODEL", DEFAULT_GPT_IMAGE_MODEL
            ).strip(),
            download_timeout_seconds=env_int(
                "OPENAI_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", 60, 10, 180
            ),
        )

    @property
    def is_configured(self) -> bool:
        return self._client is not None and bool(self.model)

    async def generate(self, prompt: str) -> GPTGeneratedImage:
        if not self.is_configured:
            raise GPTImageError("GPT 生图服务尚未配置。")

        try:
            response = await self._client.images.generate(
                model=self.model,
                prompt=SAFE_IMAGE_INSTRUCTION + prompt,
                n=1,
                size=GPT_IMAGE_SIZE,
                quality=GPT_IMAGE_QUALITY,
                output_format=GPT_IMAGE_OUTPUT_FORMAT,
                output_compression=GPT_IMAGE_OUTPUT_COMPRESSION,
                moderation="auto",
            )
        except APITimeoutError as exc:
            raise GPTImageError("GPT 生图请求超时。") from exc
        except APIConnectionError as exc:
            raise GPTImageError("无法连接 GPT 生图服务。") from exc
        except APIStatusError as exc:
            logger.warning(
                "GPT 生图接口返回 HTTP %s request_id=%s detail=%s",
                exc.status_code,
                _status_error_request_id(exc),
                _status_error_detail(exc),
            )
            raise GPTImageError("GPT 生图接口拒绝了本次请求。") from exc

        return await self._image_from_response(response)

    async def edit(
        self,
        prompt: str,
        reference_data: bytes,
        reference_mime_type: str,
    ) -> GPTGeneratedImage:
        if not self.is_configured:
            raise GPTImageError("GPT 生图服务尚未配置。")
        extension = REFERENCE_IMAGE_EXTENSIONS.get(reference_mime_type)
        if (
            extension is None
            or not reference_data
            or len(reference_data) > GPT_IMAGE_MAX_BYTES
        ):
            raise GPTImageError("GPT 参考图片格式或大小不合法。")

        try:
            response = await self._client.images.edit(
                model=self.model,
                image=(
                    f"reference.{extension}",
                    reference_data,
                    reference_mime_type,
                ),
                prompt=SAFE_IMAGE_INSTRUCTION + prompt,
                n=1,
                size=GPT_IMAGE_SIZE,
                quality=GPT_IMAGE_QUALITY,
                input_fidelity=GPT_IMAGE_INPUT_FIDELITY,
                output_format=GPT_IMAGE_OUTPUT_FORMAT,
                output_compression=GPT_IMAGE_OUTPUT_COMPRESSION,
            )
        except APITimeoutError as exc:
            raise GPTImageError("GPT 图生图请求超时。") from exc
        except APIConnectionError as exc:
            raise GPTImageError("无法连接 GPT 图生图服务。") from exc
        except APIStatusError as exc:
            logger.warning(
                "GPT 图生图接口返回 HTTP %s request_id=%s detail=%s",
                exc.status_code,
                _status_error_request_id(exc),
                _status_error_detail(exc),
            )
            raise GPTImageError("GPT 图生图接口拒绝了本次请求。") from exc

        return await self._image_from_response(response)

    async def _image_from_response(self, response: Any) -> GPTGeneratedImage:
        item = self._first_image(response)
        encoded = getattr(item, "b64_json", None)
        if isinstance(encoded, str) and encoded:
            return await asyncio.to_thread(
                self._normalize_for_qq,
                self._decode_image(encoded),
            )

        url = getattr(item, "url", None)
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            return await self._download_image(url)
        raise GPTImageError("GPT 生图响应中没有可用的图片数据。")

    @staticmethod
    def _first_image(response: Any) -> Any:
        data = getattr(response, "data", None)
        if not isinstance(data, list) or not data:
            raise GPTImageError("GPT 生图响应中没有图片。")
        return data[0]

    @staticmethod
    def _decode_image(encoded: str) -> bytes:
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise GPTImageError("GPT 返回了无效的图片数据。") from exc
        if not data or len(data) > GPT_IMAGE_MAX_BYTES:
            raise GPTImageError("GPT 返回的图片大小不合法。")
        return data

    @staticmethod
    def _normalize_for_qq(data: bytes) -> GPTGeneratedImage:
        try:
            with Image.open(BytesIO(data)) as source:
                source_width, source_height = source.size
                source_format = source.format or "unknown"
                if (
                    source_width < 1
                    or source_height < 1
                    or source_width * source_height
                    > GPT_IMAGE_MAX_SOURCE_PIXELS
                ):
                    raise GPTImageError("GPT 返回的图片尺寸不合法。")

                source.load()
                oriented = ImageOps.exif_transpose(source)
                if "A" in oriented.getbands():
                    rgba = oriented.convert("RGBA")
                    background = Image.new("RGBA", rgba.size, "white")
                    background.alpha_composite(rgba)
                    rgb = background.convert("RGB")
                else:
                    rgb = oriented.convert("RGB")

                fitted = ImageOps.contain(
                    rgb,
                    GPT_IMAGE_TARGET_SIZE,
                    Image.Resampling.LANCZOS,
                )
                normalized = Image.new("RGB", GPT_IMAGE_TARGET_SIZE, "white")
                offset = (
                    (GPT_IMAGE_TARGET_SIZE[0] - fitted.width) // 2,
                    (GPT_IMAGE_TARGET_SIZE[1] - fitted.height) // 2,
                )
                normalized.paste(fitted, offset)
                output = BytesIO()
                normalized.save(
                    output,
                    format="JPEG",
                    quality=GPT_IMAGE_OUTPUT_COMPRESSION,
                    optimize=True,
                )
        except GPTImageError:
            raise
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise GPTImageError("GPT 返回的图片无法解析。") from exc

        normalized_data = output.getvalue()
        if not normalized_data or len(normalized_data) > GPT_IMAGE_MAX_BYTES:
            raise GPTImageError("规范化后的 GPT 图片大小不合法。")
        logger.info(
            "GPT 图片已规范化：source=%sx%s/%s bytes=%s output=%sx%s/jpeg bytes=%s",
            source_width,
            source_height,
            source_format,
            len(data),
            GPT_IMAGE_TARGET_SIZE[0],
            GPT_IMAGE_TARGET_SIZE[1],
            len(normalized_data),
        )
        return GPTGeneratedImage(normalized_data, GPT_IMAGE_MIME_TYPE)

    async def _download_image(self, url: str) -> GPTGeneratedImage:
        timeout = aiohttp.ClientTimeout(total=self._download_timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status >= 400:
                        raise GPTImageError(
                            f"下载 GPT 图片失败（HTTP {response.status}）。"
                        )
                    data = await response.read()
                    mime_type = response.headers.get(
                        "Content-Type", "image/png"
                    ).split(";", 1)[0]
        except GPTImageError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise GPTImageError("下载 GPT 图片失败。") from exc

        if not data or len(data) > GPT_IMAGE_MAX_BYTES:
            raise GPTImageError("GPT 返回的图片大小不合法。")
        if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise GPTImageError("GPT 返回了不支持的图片格式。")
        return await asyncio.to_thread(self._normalize_for_qq, data)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
