"""OpenAI Responses API backend with strict structured outputs."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)

from aiqq.exceptions import AgentUnavailable
from aiqq.logic.models import ImageAsset
from aiqq.logic.ports import AgentTurnResult, ProgressCallback
from aiqq.services.images.validation import InvalidImage, decode_base64_image


logger = logging.getLogger(__name__)


class ResponsesBackend:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: int = 280,
        max_output_tokens: int = 1000,
        max_concurrent: int = 3,
        client: AsyncOpenAI | Any | None = None,
    ) -> None:
        if not api_key.strip() and client is None:
            raise ValueError("api_key must not be empty")
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._owns_client = client is None
        self._client = client or AsyncOpenAI(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
        )

    async def run(
        self,
        *,
        instructions: str,
        model_input: str,
        output_schema: Mapping[str, Any],
        enable_web_search: bool = False,
        enable_gpt_image_skill: bool = False,
        on_progress: ProgressCallback | None = None,
        input_images: Sequence[ImageAsset] = (),
    ) -> AgentTurnResult:
        del enable_gpt_image_skill, on_progress
        schema = _normalize_output_schema(output_schema)
        request: dict[str, Any] = {
            "model": self._model,
            "instructions": instructions,
            "input": _format_input(model_input, input_images),
            "max_output_tokens": self._max_output_tokens,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "aiqq_output",
                    "schema": schema,
                    "strict": True,
                }
            },
        }
        tools: list[dict[str, str]] = []
        if enable_web_search:
            tools.append({"type": "web_search"})
        if tools:
            request["tools"] = tools
        try:
            async with self._semaphore:
                response = await self._client.responses.create(**request)
        except (APIConnectionError, APIStatusError, APITimeoutError, RateLimitError) as exc:
            logger.warning(
                "event=responses_api_unavailable error_type=%s", type(exc).__name__
            )
            raise AgentUnavailable("Responses API request failed") from exc
        except Exception as exc:
            logger.warning(
                "event=responses_api_failed error_type=%s", type(exc).__name__
            )
            raise AgentUnavailable("Responses API request failed") from exc

        text = getattr(response, "output_text", "")
        if not isinstance(text, str) or not text.strip():
            raise AgentUnavailable("Responses API returned no structured text")
        images: list[ImageAsset] = []
        image_error = None
        for item in getattr(response, "output", ()):
            item_type = _field(item, "type")
            if item_type != "image_generation_call" or len(images) >= 1:
                continue
            encoded = _field(item, "result")
            if not isinstance(encoded, str) or not encoded:
                image_error = "图片生成结果为空。"
                continue
            try:
                images.append(decode_base64_image(encoded))
            except InvalidImage:
                image_error = "图片生成结果无法使用。"
        usage_value = getattr(response, "usage", None)
        usage = _model_dump(usage_value)
        return AgentTurnResult(
            text=text.strip(),
            images=tuple(images),
            usage=usage,
            image_error=image_error,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.close()


def _normalize_output_schema(output_schema: Mapping[str, Any]) -> dict[str, Any]:
    if not output_schema:
        raise ValueError("output_schema is required")
    try:
        schema = json.loads(json.dumps(output_schema))
    except (TypeError, ValueError) as exc:
        raise ValueError("output_schema must be JSON serializable") from exc
    if (
        not isinstance(schema, dict)
        or schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
        or not isinstance(schema.get("required"), list)
    ):
        raise ValueError("output_schema must be a strict object schema")
    return schema


def _format_input(
    model_input: str, input_images: Sequence[ImageAsset]
) -> str | list[dict[str, Any]]:
    if not input_images:
        return model_input
    content: list[dict[str, Any]] = [{"type": "input_text", "text": model_input}]
    for image in input_images[:1]:
        encoded = base64.b64encode(image.data).decode("ascii")
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:{image.mime_type};base64,{encoded}",
                "detail": "high",
            }
        )
    return [{"role": "user", "content": content}]


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _model_dump(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return value
    dump = getattr(value, "model_dump", None)
    return dump() if callable(dump) else None
