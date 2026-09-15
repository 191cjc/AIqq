"""Safe downloader for image URLs selected by the conversation agent."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver

from aiqq.logic.models import ImageAsset
from aiqq.services.images.validation import InvalidImage, MAX_IMAGE_BYTES, validate_image


logger = logging.getLogger(__name__)
MAX_REDIRECTS = 3
READ_CHUNK_SIZE = 64 * 1024


class WebImageError(RuntimeError):
    """Raised when an untrusted image URL cannot produce a safe image."""


class PublicOnlyResolver(AbstractResolver):
    """Reject DNS answers that could route a request into a private network."""

    def __init__(self) -> None:
        self._resolver = aiohttp.DefaultResolver()

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_UNSPEC,
    ) -> list[dict[str, object]]:
        addresses = await self._resolver.resolve(host, port, family)
        if not addresses:
            raise OSError("image host did not resolve")
        for address in addresses:
            try:
                resolved_ip = ipaddress.ip_address(str(address["host"]))
            except (KeyError, ValueError) as exc:
                raise OSError("image host returned an invalid address") from exc
            if not resolved_ip.is_global:
                raise OSError("image host resolved to a non-public address")
        return addresses

    async def close(self) -> None:
        await self._resolver.close()


class WebImageService:
    def __init__(self, *, timeout_seconds: int = 20) -> None:
        if timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        self._timeout_seconds = timeout_seconds
        self._session: aiohttp.ClientSession | None = None

    async def download(self, url: str) -> ImageAsset:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await self._download(url)
        except TimeoutError as exc:
            raise WebImageError("web image download timed out") from exc

    async def _download(self, url: str) -> ImageAsset:
        current_url = validate_public_https_url(url)
        session = await self._get_session()

        for redirect_count in range(MAX_REDIRECTS + 1):
            try:
                async with session.get(
                    current_url,
                    allow_redirects=False,
                    headers={"Accept": "image/*", "User-Agent": "AiQQ/2"},
                ) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        if redirect_count == MAX_REDIRECTS:
                            raise WebImageError("web image redirected too many times")
                        location = response.headers.get("Location")
                        if not location:
                            raise WebImageError("web image redirect has no location")
                        current_url = validate_public_https_url(
                            urljoin(current_url, location)
                        )
                        continue
                    if response.status < 200 or response.status >= 300:
                        raise WebImageError(
                            f"web image returned HTTP {response.status}"
                        )
                    content_type = response.headers.get("Content-Type", "")
                    if not content_type.split(";", 1)[0].strip().lower().startswith(
                        "image/"
                    ):
                        raise WebImageError("web resource is not an image")
                    if (
                        response.content_length is not None
                        and response.content_length > MAX_IMAGE_BYTES
                    ):
                        raise WebImageError("web image exceeds the size limit")

                    data = bytearray()
                    async for chunk in response.content.iter_chunked(READ_CHUNK_SIZE):
                        data.extend(chunk)
                        if len(data) > MAX_IMAGE_BYTES:
                            raise WebImageError("web image exceeds the size limit")
            except WebImageError:
                raise
            except (aiohttp.ClientError, OSError) as exc:
                raise WebImageError("web image could not be downloaded") from exc

            try:
                return validate_image(bytes(data))
            except InvalidImage as exc:
                raise WebImageError("web resource is not a valid supported image") from exc

        raise WebImageError("web image download did not complete")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                resolver=PublicOnlyResolver(),
                limit=4,
                ttl_dns_cache=0,
            )
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout_seconds),
                connector=connector,
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


def validate_public_https_url(url: str) -> str:
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise WebImageError("web image URL is invalid")
    if url != url.strip() or any(ord(character) < 32 for character in url):
        raise WebImageError("web image URL is invalid")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise WebImageError("web image URL is invalid") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise WebImageError("web image URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise WebImageError("web image URL cannot contain credentials")
    if port not in {None, 443}:
        raise WebImageError("web image URL must use the HTTPS port")
    try:
        literal_ip = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        if not literal_ip.is_global:
            raise WebImageError("web image URL is not public")
    return url
