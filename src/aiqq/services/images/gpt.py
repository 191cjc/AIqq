"""GPT Images adapter implementing the generated-image service port."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import re
from io import BytesIO
from time import monotonic
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
)
from PIL import Image, ImageOps, UnidentifiedImageError

from aiqq.exceptions import ImageGenerationUnavailable
from aiqq.logic.models import ImageAction, ImageAsset
from aiqq.services.images.validation import MAX_IMAGE_BYTES


logger = logging.getLogger(__name__)
IMAGE_SIZE = "1024x1024"
IMAGE_QUALITY = "medium"
OUTPUT_COMPRESSION = 85
TARGET_SIZE = (1024, 1024)
BAD_GATEWAY_RETRY_DELAYS = (0.5, 1.0)
INPUT_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
SAFE_IMAGE_INSTRUCTION = (
    "Create a safe-for-work image. Do not include nudity, sexual content, fetish "
    "content, or sexualized minors. User request: "
)
MAX_ERROR_BODY_BYTES = 16 * 1024
MAX_DIAGNOSTIC_CHARS = 1000
DETAIL_OMITTED = "detail_omitted"
ERROR_TOKENS = {
    "authentication_error", "invalid_api_key", "permission_denied",
    "permission_error", "model_not_found", "not_found", "invalid_request_error",
    "invalid_request", "unsupported_parameter", "unknown_parameter",
    "rate_limit_exceeded", "rate_limit_error", "insufficient_quota",
    "server_error", "internal_error", "upstream_error", "service_unavailable",
    "content_policy_violation", "moderation_blocked", "image_generation_failed",
}
ERROR_PARAMS = {
    "model", "prompt", "image", "mask", "n", "size", "quality",
    "input_fidelity", "output_format", "output_compression", "response_format",
    "moderation", "background",
}


class GPTImageError(ImageGenerationUnavailable):
    """GPT adapter failure; provider diagnostics stay in administrator logs."""


class GPTImageService:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-image-2",
        timeout_seconds: int = 240,
        client: AsyncOpenAI | Any | None = None,
    ) -> None:
        if not api_key.strip() and client is None:
            raise ValueError("api_key must not be empty")
        self.model = model
        self._api_key = api_key
        self._owns_client = client is None
        self._client = client or AsyncOpenAI(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            timeout=float(timeout_seconds),
            max_retries=0,
        )
        self._base_url = str(self._client.base_url)

    async def generate(
        self,
        action: ImageAction,
        *,
        reference_image: ImageAsset | None = None,
    ) -> ImageAsset:
        attempt_id = uuid4().hex
        operation = "edit" if action.mode == "edit" else "generate"
        path = "/images/edits" if operation == "edit" else "/images/generations"
        secrets = (self._api_key, action.prompt)
        fields = {
            "attempt_id": attempt_id,
            "operation": operation,
            "model": _safe_label(self.model, secrets),
            "endpoint": _safe_endpoint(self._base_url, path, secrets),
        }
        started = monotonic()
        status_code = None
        provider_request_id = None
        outcome = "failed"
        kind = "unknown"
        diagnostics: dict[str, object] = {}
        _log_event("gpt_image_started", fields)
        try:
            request_params = {
                "model": self.model,
                "prompt": SAFE_IMAGE_INSTRUCTION + action.prompt,
                "n": 1,
                "size": IMAGE_SIZE,
                "quality": IMAGE_QUALITY,
                "output_format": "jpeg",
                "output_compression": OUTPUT_COMPRESSION,
            }
            if operation == "edit":
                if reference_image is None:
                    raise GPTImageError(
                        "reference image is required for editing", kind="invalid_request"
                    )
                extension = INPUT_EXTENSIONS.get(reference_image.mime_type)
                if extension is None:
                    raise GPTImageError(
                        "reference image format is unsupported", kind="invalid_request"
                    )
                request = self._client.images.with_raw_response.edit
                request_params.update(
                    image=(
                        f"reference.{extension}",
                        reference_image.data,
                        reference_image.mime_type,
                    ),
                    input_fidelity="high",
                )
            else:
                request = self._client.images.with_raw_response.generate
                request_params["moderation"] = "auto"
            for retry_index in range(len(BAD_GATEWAY_RETRY_DELAYS) + 1):
                try:
                    raw = await request(**request_params)
                    break
                except APIStatusError as exc:
                    if exc.status_code != 502 or retry_index == len(BAD_GATEWAY_RETRY_DELAYS):
                        raise
                    delay = BAD_GATEWAY_RETRY_DELAYS[retry_index]
                    # Retry diagnostics must not become a later timeout's metadata.
                    _log_event(
                        "gpt_image_retry",
                        {
                            **fields,
                            "elapsed_ms": round((monotonic() - started) * 1000),
                            "retry_number": retry_index + 1,
                            "delay_seconds": delay,
                            "status": 502,
                            "error_kind": "upstream",
                            "provider_request_id": _request_id(exc.response.headers, secrets),
                            **_error_diagnostics(exc.response, self.model, secrets),
                        },
                        failed=True,
                    )
                    await asyncio.sleep(delay)
            status_code = raw.status_code
            provider_request_id = _request_id(raw.headers, secrets)
            try:
                response = raw.parse()
            except (ValueError, TypeError, APIResponseValidationError) as exc:
                raise GPTImageError(
                    "GPT image response is invalid", kind="invalid_response"
                ) from exc
            asset = await asyncio.to_thread(_decode_and_normalize, response)
            outcome = "succeeded"
            kind = "none"
            return asset
        except asyncio.CancelledError:
            outcome = "cancelled"
            kind = "cancelled"
            raise
        except Exception as exc:
            if isinstance(exc, GPTImageError):
                kind = exc.kind
            elif isinstance(exc, APIStatusError):
                status_code = exc.status_code
                kind = _status_kind(status_code)
                provider_request_id = _request_id(exc.response.headers, secrets)
                diagnostics = _error_diagnostics(exc.response, self.model, secrets)
            elif isinstance(exc, APITimeoutError):
                kind = "timeout"
            elif isinstance(exc, APIConnectionError):
                kind = "connection"
            elif isinstance(exc, APIResponseValidationError):
                kind = "invalid_response"
                status_code = exc.status_code
                provider_request_id = _request_id(exc.response.headers, secrets)
            # Never log the exception or its traceback: SDK exceptions embed bodies.
            raise GPTImageError(
                "GPT image service is unavailable",
                kind=kind,
                attempt_id=attempt_id,
                status_code=status_code,
                provider_request_id=provider_request_id,
            ) from exc
        finally:
            _log_event(
                "gpt_image_finished",
                {
                    **fields,
                    "elapsed_ms": round((monotonic() - started) * 1000),
                    "outcome": outcome,
                    "status": status_code,
                    "error_kind": kind,
                    "provider_request_id": provider_request_id,
                    **diagnostics,
                },
                failed=outcome == "failed",
            )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.close()


def _log_event(event: str, fields: dict[str, object], *, failed: bool = False) -> None:
    logger.log(
        logging.WARNING if failed else logging.INFO,
        "event=%s %s",
        event,
        json.dumps(fields, ensure_ascii=True, separators=(",", ":")),
    )


def _safe_label(value: object, secrets: tuple[str, ...]) -> str:
    """Only retain short identifiers, never free-form provider text."""
    if not isinstance(value, str) or not value:
        return DETAIL_OMITTED
    if len(value) > 128 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        return DETAIL_OMITTED
    if re.search(r"(?i)(sk-|bearer|api[_-]?key|token|secret)", value):
        return DETAIL_OMITTED
    if any(secret and secret.casefold() in value.casefold() for secret in secrets):
        return DETAIL_OMITTED
    # Long opaque strings can be image data or tokens; keep common hex request IDs.
    for part in re.findall(r"[A-Za-z0-9_]+", value):
        if len(part) >= 48 and not re.fullmatch(r"[a-fA-F0-9]{48,64}", part):
            return DETAIL_OMITTED
    return value


def _safe_endpoint(base_url: str, path: str, secrets: tuple[str, ...]) -> str:
    try:
        parsed = urlsplit(base_url)
        host = parsed.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if parsed.port:
            host += f":{parsed.port}"
        endpoint = urlunsplit(
            (parsed.scheme, host, parsed.path.rstrip("/") + path, "", "")
        )
    except ValueError:
        return DETAIL_OMITTED
    if len(endpoint) > 300 or any(
        secret and secret.casefold() in endpoint.casefold() for secret in secrets
    ):
        return DETAIL_OMITTED
    if any(ord(char) < 32 or ord(char) == 127 for char in endpoint):
        return DETAIL_OMITTED
    return endpoint


def _request_id(headers: httpx.Headers, secrets: tuple[str, ...]) -> str | None:
    value = headers.get("x-request-id") or headers.get("request-id")
    return _safe_label(value, secrets) if value else None


def _status_kind(status: int) -> str:
    if status in {401, 403}:
        return "authentication"
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limited"
    if status in {400, 422}:
        return "invalid_request"
    if status >= 500:
        return "upstream"
    return "unknown"


def _error_diagnostics(
    response: httpx.Response, model: str, secrets: tuple[str, ...]
) -> dict[str, object]:
    """Extract bounded known error fields; never retain arbitrary response prose."""
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    fields: dict[str, object] = {
        "body_bytes": len(response.content),
        "content_type": content_type if content_type in {
            "application/json", "application/problem+json", "text/html", "text/plain"
        } else "other",
    }
    if len(response.content) > MAX_ERROR_BODY_BYTES:
        return {**fields, "detail_omitted": "oversize_body"}
    if content_type not in {"application/json", "application/problem+json"}:
        return {**fields, "detail_omitted": "non_json_body"}
    try:
        body = response.json()
    except (ValueError, RecursionError):
        return {**fields, "detail_omitted": "invalid_json"}
    if not isinstance(body, dict):
        return {**fields, "detail_omitted": "invalid_error_object"}
    body = body.get("error", body)
    if not isinstance(body, dict):
        return {**fields, "detail_omitted": "invalid_error_object"}
    for name in ("type", "code", "param"):
        value = body.get(name)
        if isinstance(value, str):
            allowed = ERROR_PARAMS if name == "param" else ERROR_TOKENS
            fields[f"provider_{name}"] = (
                value if value in allowed and _safe_label(value, secrets) != DETAIL_OMITTED
                else DETAIL_OMITTED
            )
    message = body.get("message")
    if isinstance(message, str):
        # Matching complete known templates prevents partial prompt/token echoes.
        normalized = " ".join(message.split()) if len(message) <= MAX_DIAGNOSTIC_CHARS else ""
        if normalized == f'Model "{model}" is not supported by any configured account in this group':
            fields["provider_message"] = (
                "Configured model is not supported by any configured account in this group"
            )
        elif normalized in {
            "Internal server error", "Rate limit exceeded", "Service unavailable",
            "Invalid API key", "Incorrect API key provided", "Model not found",
        }:
            fields["provider_message"] = normalized
        else:
            fields["provider_message"] = DETAIL_OMITTED
    if not any(key.startswith("provider_") for key in fields):
        fields["detail_omitted"] = "no_safe_error_fields"
    return fields


def _decode_and_normalize(response: Any) -> ImageAsset:
    items = getattr(response, "data", None)
    if not isinstance(items, list) or not items:
        raise GPTImageError("GPT image response contains no image", kind="invalid_response")
    encoded = getattr(items[0], "b64_json", None)
    if not isinstance(encoded, str) or not encoded:
        raise GPTImageError("GPT image response contains no Base64 image", kind="invalid_response")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise GPTImageError("GPT image response is invalid Base64", kind="invalid_response") from exc
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise GPTImageError("GPT image response size is invalid", kind="invalid_response")
    try:
        with Image.open(BytesIO(data)) as source:
            width, height = source.size
            if width < 1 or height < 1 or width * height > 4096 * 4096:
                raise GPTImageError("GPT image dimensions are invalid", kind="invalid_response")
            source.load()
            oriented = ImageOps.exif_transpose(source)
            if "A" in oriented.getbands():
                rgba = oriented.convert("RGBA")
                background = Image.new("RGBA", rgba.size, "white")
                background.alpha_composite(rgba)
                rgb = background.convert("RGB")
            else:
                rgb = oriented.convert("RGB")
            fitted = ImageOps.contain(rgb, TARGET_SIZE, Image.Resampling.LANCZOS)
            normalized = Image.new("RGB", TARGET_SIZE, "white")
            normalized.paste(
                fitted,
                (
                    (TARGET_SIZE[0] - fitted.width) // 2,
                    (TARGET_SIZE[1] - fitted.height) // 2,
                ),
            )
            output = BytesIO()
            normalized.save(
                output,
                format="JPEG",
                quality=OUTPUT_COMPRESSION,
                optimize=True,
            )
    except GPTImageError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
        raise GPTImageError("GPT image response cannot be decoded", kind="invalid_response") from exc
    normalized_data = output.getvalue()
    if not normalized_data or len(normalized_data) > MAX_IMAGE_BYTES:
        raise GPTImageError("normalized GPT image size is invalid", kind="invalid_response")
    return ImageAsset(normalized_data, "image/jpeg", *TARGET_SIZE)
