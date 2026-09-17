"""NovelAI MCP adapter with strict image validation at the trust boundary."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Protocol

import aiohttp

from aiqq.exceptions import ImageGenerationUnavailable
from aiqq.logic.models import ImageAsset

from .validation import InvalidImage, decode_base64_image


logger = logging.getLogger(__name__)
MCP_PROTOCOL_VERSION = "2025-06-18"
NOVELAI_MODEL = "v4.5-full"
NOVELAI_IMAGE_SIZES = {
    "square": (1024, 1024),
    "portrait": (832, 1216),
    "landscape": (1216, 832),
}
NOVELAI_STEPS = 28
NOVELAI_CFG_RESCALE = 0.5
SAFE_PROMPT_PREFIX = "rating:general, safe, sfw"
SAFE_NEGATIVE_PROMPT = (
    "nsfw, explicit, nude, naked, nipples, areolae, genitals, sex, "
    "sexual_intercourse, masturbation, sexual_fluids, erotic, suggestive, "
    "fetish, lingerie, underwear, see-through_clothes, cleavage, loli, shota, "
    "child sexualization"
)


class NovelAIError(ImageGenerationUnavailable, RuntimeError):
    """Expected NovelAI failure, compatible with existing RuntimeError catches."""


class NovelAIMCPTransport(Protocol):
    async def list_tools(self) -> dict[str, Any]: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...

    async def close(self) -> None: ...


def _parse_sse(body: str) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    data_lines: list[str] = []

    def finish_event() -> None:
        if not data_lines:
            return
        raw_data = "\n".join(data_lines)
        data_lines.clear()
        if raw_data == "[DONE]":
            return
        value = json.loads(raw_data)
        if isinstance(value, dict):
            messages.append(value)

    for line in body.splitlines():
        if not line:
            finish_event()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    finish_event()
    if not messages:
        raise NovelAIError("NovelAI MCP returned an empty event stream")
    return messages[-1]


def _decode_mcp_response(body: bytes, content_type: str) -> dict[str, Any]:
    if not body:
        return {}
    try:
        text = body.decode("utf-8")
        value = (
            _parse_sse(text)
            if "text/event-stream" in content_type or text.startswith("event:")
            else json.loads(text)
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NovelAIError("NovelAI MCP returned an invalid response") from exc
    if not isinstance(value, dict):
        raise NovelAIError("NovelAI MCP response must be an object")
    return value


class MCPClient:
    def __init__(self, *, url: str, token: str, timeout_seconds: int) -> None:
        if not url.strip() or not token.strip():
            raise ValueError("NovelAI MCP URL and token are required")
        self._url = url.rstrip("/")
        self._token = token
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None

    async def list_tools(self) -> dict[str, Any]:
        result = await self._with_connection("tools/list", {})
        if not isinstance(result, dict):
            raise NovelAIError("NovelAI tools/list result must be an object")
        return result

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return await self._with_connection(
            "tools/call", {"name": name, "arguments": arguments}
        )

    async def _with_connection(self, method: str, params: dict[str, Any]) -> Any:
        initialize, session_id = await self._request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "aiqq", "version": "1.0.0"},
                },
            },
            None,
        )
        self._extract_result(initialize)
        await self._request(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            session_id,
            expect_response=False,
        )
        response, _ = await self._request(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": method,
                "params": params,
            },
            session_id,
        )
        return self._extract_result(response)

    async def _request(
        self,
        payload: dict[str, Any],
        session_id: str | None,
        *,
        expect_response: bool = True,
    ) -> tuple[dict[str, Any], str | None]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "AiQQ/1.0",
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        try:
            session = await self._get_session()
            async with session.post(self._url, headers=headers, json=payload) as response:
                body = await response.read()
                if response.status >= 400:
                    raise NovelAIError(
                        f"NovelAI MCP request failed with HTTP {response.status}"
                    )
                next_session_id = response.headers.get("Mcp-Session-Id", session_id)
                if not expect_response:
                    return {}, next_session_id
                decoded = _decode_mcp_response(
                    body, response.headers.get("Content-Type", "")
                )
                return decoded, next_session_id
        except asyncio.TimeoutError as exc:
            raise NovelAIError("NovelAI MCP request timed out") from exc
        except aiohttp.ClientError as exc:
            raise NovelAIError("NovelAI MCP connection failed") from exc

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    @staticmethod
    def _extract_result(response: dict[str, Any]) -> Any:
        if "error" in response:
            raise NovelAIError("NovelAI MCP returned an RPC error")
        if "result" not in response:
            raise NovelAIError("NovelAI MCP response omitted result")
        return response["result"]

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


class NovelAIService:
    def __init__(
        self,
        client: NovelAIMCPTransport | None,
        *,
        max_concurrent: int = 1,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("NovelAI concurrency must be positive")
        self._client = client
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._supports_steps: bool | None = None

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    @property
    def is_ready(self) -> bool:
        return self.is_configured and self._supports_steps is not None

    async def initialize(self) -> None:
        logger.info(
            "event=novelai_initialization_started configured=%s", self.is_configured
        )
        await self.check_connection()

    async def check_connection(self) -> bool:
        self._supports_steps = None
        if self._client is None:
            logger.warning(
                "event=novelai_connection_check_failed configured=false "
                "ready=false error_kind=not_configured"
            )
            return False
        try:
            tools = await self._client.list_tools()
            supports_steps = self._tool_supports_steps(tools)
        except Exception as exc:
            logger.warning(
                "event=novelai_connection_check_failed configured=true "
                "ready=false error_kind=not_ready error_type=%s",
                type(exc).__name__,
            )
            return False
        if supports_steps is None:
            logger.warning(
                "event=novelai_connection_check_failed configured=true "
                "ready=false error_kind=generate_image_not_advertised"
            )
            return False
        self._supports_steps = supports_steps
        if not self._supports_steps:
            logger.warning(
                "event=novelai_steps_not_advertised default_steps=%s",
                NOVELAI_STEPS,
            )
        logger.info(
            "event=novelai_connection_ready configured=true ready=true "
            "supports_steps=%s", self._supports_steps,
        )
        return True

    async def generate(
        self, prompt: str, *, orientation: str = "square"
    ) -> ImageAsset:
        if self._client is None:
            raise NovelAIError(
                "NovelAI service is not configured", kind="not_configured"
            )
        if self._supports_steps is None:
            raise NovelAIError("NovelAI service is not ready", kind="not_ready")
        try:
            width, height = NOVELAI_IMAGE_SIZES[orientation]
        except KeyError as exc:
            raise NovelAIError("NovelAI image orientation is unsupported") from exc

        arguments: dict[str, Any] = {
            "prompt": f"{SAFE_PROMPT_PREFIX}, {prompt}",
            "negative_prompt": SAFE_NEGATIVE_PROMPT,
            "model": NOVELAI_MODEL,
            "width": width,
            "height": height,
            "seed": None,
            "quality_toggle": False,
            "variety_boost": True,
            "cfg_rescale": NOVELAI_CFG_RESCALE,
        }
        if self._supports_steps:
            arguments["steps"] = NOVELAI_STEPS
        async with self._semaphore:
            result = await self._client.call_tool("generate_image", arguments)
        if isinstance(result, dict) and result.get("isError") is True:
            raise NovelAIError("NovelAI generation failed")
        image = _find_inline_image(result)
        if image is None:
            raise NovelAIError("NovelAI response contains no image")
        return image

    @staticmethod
    def _tool_supports_steps(tools_response: dict[str, Any]) -> bool | None:
        tools = tools_response.get("tools", [])
        if not isinstance(tools, list):
            return None
        for tool in tools:
            if not isinstance(tool, dict) or tool.get("name") != "generate_image":
                continue
            schema = tool.get("inputSchema", {})
            properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
            return isinstance(properties, dict) and "steps" in properties
        return None

    async def close(self) -> None:
        self._supports_steps = None
        if self._client is not None:
            await self._client.close()


def _find_inline_image(value: Any) -> ImageAsset | None:
    if isinstance(value, dict):
        mime_type = value.get("mimeType") or value.get("mime_type")
        encoded = value.get("data")
        if value.get("type") == "image" and isinstance(encoded, str):
            if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
                raise NovelAIError("NovelAI declared an unsupported image format")
            try:
                image = decode_base64_image(encoded)
            except InvalidImage as exc:
                raise NovelAIError("NovelAI returned invalid image data") from exc
            if image.mime_type != mime_type:
                raise NovelAIError("NovelAI image content does not match its MIME type")
            return image
        for child in value.values():
            image = _find_inline_image(child)
            if image is not None:
                return image
    elif isinstance(value, list):
        for child in value:
            image = _find_inline_image(child)
            if image is not None:
                return image
    return None
