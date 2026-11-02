"""HTTP client for the GPT relay ``/usage`` endpoint."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from aiqq.logic.gpt_usage import GPTUsageUnavailable


logger = logging.getLogger(__name__)


class GPTUsageClient:
    """Fetch per-key usage from ``{base_url}/usage`` with a bearer key."""

    def __init__(self, base_url: str, *, timeout_seconds: float = 20) -> None:
        self._url = f"{base_url.rstrip('/')}/usage"
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None

    async def fetch(self, api_key: str) -> dict[str, Any]:
        session = self._get_session()
        try:
            async with session.get(
                self._url, headers={"Authorization": f"Bearer {api_key}"}
            ) as response:
                if response.status != 200:
                    logger.warning(
                        "event=gpt_usage_http_error status=%s", response.status
                    )
                    raise GPTUsageUnavailable(f"HTTP {response.status}")
                payload = await response.json(content_type=None)
        except GPTUsageUnavailable:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            logger.warning(
                "event=gpt_usage_request_failed error_type=%s", type(exc).__name__
            )
            raise GPTUsageUnavailable(type(exc).__name__) from exc
        if not isinstance(payload, dict):
            raise GPTUsageUnavailable("unexpected payload")
        return payload

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
