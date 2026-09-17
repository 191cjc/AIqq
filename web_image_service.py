import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlparse

import aiohttp
from aiohttp.abc import AbstractResolver

from ai_service import env_int
from codex_image import (
    CODEX_IMAGE_MAX_BYTES,
    CodexGeneratedImage,
    CodexImageError,
    validate_codex_image,
)


logger = logging.getLogger(__name__)
MAX_IMAGE_CANDIDATES = 5
MAX_REDIRECTS = 3


class WebImageError(RuntimeError):
    pass


class PublicOnlyResolver(AbstractResolver):
    def __init__(self):
        self._resolver = aiohttp.DefaultResolver()

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_UNSPEC,
    ):
        addresses = await self._resolver.resolve(host, port, family)
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address["host"])
            except ValueError as exc:
                raise OSError("图片地址无法解析。") from exc
            if not ip.is_global:
                raise OSError("图片地址不是公网地址。")
        return addresses

    async def close(self) -> None:
        await self._resolver.close()


class WebImageService:
    def __init__(self, timeout_seconds: int = 20):
        self.timeout_seconds = timeout_seconds
        self._session: aiohttp.ClientSession | None = None

    @classmethod
    def from_env(cls) -> "WebImageService":
        return cls(env_int("AIQQ_WEB_IMAGE_TIMEOUT_SECONDS", 20, 5, 60))

    async def download_best(
        self, candidates: tuple[str, ...]
    ) -> CodexGeneratedImage | None:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                for url in candidates[:MAX_IMAGE_CANDIDATES]:
                    try:
                        return await self._download(url)
                    except WebImageError as exc:
                        host = urlparse(url).hostname or "unknown"
                        logger.info(
                            "跳过不可用的搜索图片：host=%s reason=%s",
                            host,
                            exc,
                        )
        except TimeoutError:
            logger.info("搜索图片下载超过 %s 秒总时限", self.timeout_seconds)
        return None

    async def _download(self, url: str) -> CodexGeneratedImage:
        current_url = self._validated_url(url)
        session = await self._get_session()
        for redirect_count in range(MAX_REDIRECTS + 1):
            try:
                async with session.get(
                    current_url,
                    allow_redirects=False,
                    headers={"User-Agent": "AiQQ/1.0"},
                ) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        if redirect_count >= MAX_REDIRECTS:
                            raise WebImageError("图片重定向次数过多。")
                        location = response.headers.get("Location", "")
                        current_url = self._validated_url(
                            urljoin(current_url, location)
                        )
                        continue
                    if response.status >= 400:
                        raise WebImageError(f"图片下载返回 HTTP {response.status}。")
                    content_type = response.headers.get("Content-Type", "")
                    if not content_type.lower().startswith("image/"):
                        raise WebImageError("搜索结果不是图片内容。")
                    content_length = response.content_length
                    if content_length is not None and content_length > CODEX_IMAGE_MAX_BYTES:
                        raise WebImageError("搜索图片文件过大。")
                    data = bytearray()
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        data.extend(chunk)
                        if len(data) > CODEX_IMAGE_MAX_BYTES:
                            raise WebImageError("搜索图片文件过大。")
            except WebImageError:
                raise
            except (aiohttp.ClientError, TimeoutError, OSError) as exc:
                raise WebImageError("无法下载搜索图片。") from exc

            try:
                return validate_codex_image(bytes(data))
            except CodexImageError as exc:
                raise WebImageError(str(exc)) from exc

        raise WebImageError("图片下载未完成。")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            connector = aiohttp.TCPConnector(
                resolver=PublicOnlyResolver(),
                limit=4,
            )
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
            )
        return self._session

    @staticmethod
    def _validated_url(url: str) -> str:
        if not isinstance(url, str) or len(url) > 2048:
            raise WebImageError("图片地址不合法。")
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise WebImageError("图片地址必须是公网 HTTPS 地址。")
        if parsed.username or parsed.password:
            raise WebImageError("图片地址不能包含认证信息。")
        try:
            ip = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            pass
        else:
            if not ip.is_global:
                raise WebImageError("图片地址不是公网地址。")
        return url

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
