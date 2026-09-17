import asyncio
import base64
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp

from ai_service import env_int


logger = logging.getLogger(__name__)
DEFAULT_MCP_URL = "https://www.fsstudy.com.cn/mcp"
MCP_PROTOCOL_VERSION = "2025-06-18"
NOVELAI_MODEL = "v4.5-full"
NOVELAI_IMAGE_SIZES = {
    "square": (1024, 1024),
    "portrait": (832, 1216),
    "landscape": (1216, 832),
}
NOVELAI_STEPS = 28
NOVELAI_CFG_RESCALE = 0.5
NOVELAI_MAX_IMAGE_BYTES = 20 * 1024 * 1024
SAFE_PROMPT_PREFIX = "rating:general, safe, sfw"
SAFE_NEGATIVE_PROMPT = (
    # Adult-content safeguards are always applied and cannot be overridden by users.
    "nsfw, explicit, nude, naked, nipples, areolae, genitals, sex, "
    "sexual_intercourse, masturbation, sexual_fluids, erotic, suggestive, "
    "fetish, lingerie, underwear, see-through_clothes, cleavage, loli, shota, "
    "child sexualization"
)


class NovelAIError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    mime_type: str


def _parse_sse(body: str) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    data_lines: list[str] = []

    def finish_event() -> None:
        if not data_lines:
            return
        raw_data = "\n".join(data_lines)
        data_lines.clear()
        if raw_data != "[DONE]":
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
        raise NovelAIError("NovelAI MCP 返回了空事件流。")
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
        raise NovelAIError("NovelAI MCP 返回了无效响应。") from exc
    if not isinstance(value, dict):
        raise NovelAIError("NovelAI MCP 返回格式不正确。")
    return value


class MCPClient:
    def __init__(self, url: str, token: str, timeout_seconds: int):
        self.url = url
        self._token = token
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None

    async def list_tools(self) -> dict[str, Any]:
        return await self._with_connection("tools/list", {})

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return await self._with_connection(
            "tools/call", {"name": name, "arguments": arguments}
        )

    async def _with_connection(self, method: str, params: dict[str, Any]) -> Any:
        session_id: str | None = None
        request_id = 1

        initialize, session_id = await self._request(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "aiqq", "version": "1.0.0"},
                },
            },
            session_id,
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
                "id": request_id + 1,
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

        session = await self._get_session()
        try:
            async with session.post(self.url, headers=headers, json=payload) as response:
                body = await response.read()
                if response.status >= 400:
                    raise NovelAIError(
                        f"NovelAI MCP 请求失败（HTTP {response.status}）。"
                    )
                next_session_id = response.headers.get("Mcp-Session-Id", session_id)
                if not expect_response:
                    return {}, next_session_id
                return (
                    _decode_mcp_response(
                        body, response.headers.get("Content-Type", "")
                    ),
                    next_session_id,
                )
        except asyncio.TimeoutError as exc:
            raise NovelAIError("NovelAI MCP 请求超时。") from exc
        except aiohttp.ClientError as exc:
            raise NovelAIError("无法连接 NovelAI MCP 服务。") from exc

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    @staticmethod
    def _extract_result(response: dict[str, Any]) -> Any:
        if "error" in response:
            raise NovelAIError("NovelAI MCP 返回调用错误。")
        if "result" not in response:
            raise NovelAIError("NovelAI MCP 响应缺少 result。")
        return response["result"]

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


def _load_token() -> str:
    token = os.getenv("NOVELAI_MCP_TOKEN", "").strip()
    if token:
        return token

    token_file = os.getenv("NOVELAI_MCP_TOKEN_FILE", "").strip()
    if not token_file:
        token_file = str(Path.home() / ".config" / "novelai-mcp" / "token")
    try:
        return Path(token_file).expanduser().read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("无法读取 NOVELAI_MCP_TOKEN_FILE")
        return ""


def _find_inline_image(value: Any) -> GeneratedImage | None:
    if isinstance(value, dict):
        mime_type = value.get("mimeType") or value.get("mime_type")
        data = value.get("data")
        if (
            value.get("type") == "image"
            and isinstance(mime_type, str)
            and isinstance(data, str)
        ):
            try:
                decoded = base64.b64decode(data, validate=True)
            except (ValueError, TypeError) as exc:
                raise NovelAIError("NovelAI 返回的图片数据无效。") from exc
            if not decoded or len(decoded) > NOVELAI_MAX_IMAGE_BYTES:
                raise NovelAIError("NovelAI 返回的图片大小不合法。")
            if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
                raise NovelAIError("NovelAI 返回了不支持的图片格式。")
            return GeneratedImage(decoded, mime_type)

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


class NovelAIService:
    def __init__(self, client: MCPClient | Any | None, max_concurrent: int = 1):
        self._client = client
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._supports_steps: bool | None = None

    @classmethod
    def from_env(cls) -> "NovelAIService":
        token = _load_token()
        url = os.getenv("NOVELAI_MCP_URL", DEFAULT_MCP_URL).strip()
        client = None
        if token and url:
            client = MCPClient(
                url,
                token,
                env_int("AIQQ_NOVELAI_TIMEOUT_SECONDS", 240, 30, 300),
            )
        return cls(
            client,
            env_int("AIQQ_NOVELAI_MAX_CONCURRENT", 1, 1, 3),
        )

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    @property
    def is_ready(self) -> bool:
        return self.is_configured and self._supports_steps is not None

    async def check_connection(self) -> bool:
        if not self.is_configured:
            return False
        try:
            tools = await self._client.list_tools()
            self._supports_steps = self._tool_supports_steps(tools)
            if not self._supports_steps:
                logger.warning(
                    "NovelAI MCP 未公开 steps 参数，将使用已验证的服务端默认 %s 步",
                    NOVELAI_STEPS,
                )
            return True
        except NovelAIError as exc:
            logger.warning("NovelAI MCP 连接检查失败：%s", exc)
            return False

    async def generate(
        self, prompt: str, *, orientation: str = "square"
    ) -> GeneratedImage:
        if not self.is_configured:
            raise NovelAIError("NovelAI 服务尚未配置。")
        if self._supports_steps is None:
            raise NovelAIError("NovelAI MCP 尚未完成能力检查。")
        try:
            width, height = NOVELAI_IMAGE_SIZES[orientation]
        except KeyError as exc:
            raise NovelAIError("NovelAI 图片方向参数不受支持。") from exc

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
            raise NovelAIError("NovelAI 生成失败。")
        image = _find_inline_image(result)
        if image is None:
            raise NovelAIError("NovelAI 响应中没有可用的图片数据。")
        return image

    @staticmethod
    def _tool_supports_steps(tools_response: dict[str, Any]) -> bool:
        tools = tools_response.get("tools", [])
        if not isinstance(tools, list):
            return False
        for tool in tools:
            if not isinstance(tool, dict) or tool.get("name") != "generate_image":
                continue
            schema = tool.get("inputSchema", {})
            properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
            return isinstance(properties, dict) and "steps" in properties
        return False

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
