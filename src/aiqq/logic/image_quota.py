"""Shared image-generation admission and accounting rules."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date

from .models import ImageUsageDecision
from .ports import ImageUsageStore


class ImageQuotaManager:
    def __init__(
        self,
        repository: ImageUsageStore,
        *,
        daily_limit: int,
        nai_v5_daily_limit: int | None = None,
        nai_v5_whitelist: frozenset[str] = frozenset(),
        today: Callable[[], date] = date.today,
    ) -> None:
        if daily_limit < 1:
            raise ValueError("daily image limit must be positive")
        if nai_v5_daily_limit is not None and nai_v5_daily_limit < 1:
            raise ValueError("Nai v5 daily image limit must be positive")
        self._repository = repository
        self._daily_limit = daily_limit
        self._nai_v5_daily_limit = nai_v5_daily_limit
        self._nai_v5_whitelist = nai_v5_whitelist
        self._today = today
        self._active_conversation_key: str | None = None
        self._active_provider = ""
        self._active_member_openid = ""
        self._lock = asyncio.Lock()

    async def reserve_attempt(
        self, conversation_key: str, *, provider: str = "shared",
        member_openid: str = "",
    ) -> ImageUsageDecision:
        if not conversation_key:
            raise ValueError("conversation key must not be empty")
        if provider not in {"shared", "nai_v5"}:
            raise ValueError("image provider is unsupported")
        if provider == "nai_v5" and (
            not member_openid or self._nai_v5_daily_limit is None
        ):
            raise ValueError("Nai v5 quota requires a member and daily limit")
        async with self._lock:
            if self._active_conversation_key is not None:
                return ImageUsageDecision(False, "busy")
            usage_date = self._today().isoformat()
            whitelisted = (
                provider == "nai_v5" and member_openid in self._nai_v5_whitelist
            )
            if provider == "nai_v5":
                v5_count = await self._repository.nai_v5_success_count(
                    member_openid, usage_date
                )
                if not whitelisted and v5_count >= self._nai_v5_daily_limit:
                    return ImageUsageDecision(
                        False, "daily_limit", daily_limit=self._nai_v5_daily_limit
                    )
            if not whitelisted:
                success_count = await self._repository.success_count(
                    conversation_key, usage_date
                )
                if success_count >= self._daily_limit:
                    return ImageUsageDecision(
                        False, "daily_limit", daily_limit=self._daily_limit
                    )
            self._active_conversation_key = conversation_key
            self._active_provider = provider
            self._active_member_openid = member_openid
            return ImageUsageDecision(True)

    async def record_success(
        self, conversation_key: str, *, provider: str = "shared",
        member_openid: str = "",
    ) -> None:
        async with self._lock:
            if (
                self._active_conversation_key != conversation_key
                or self._active_provider != provider
                or self._active_member_openid != member_openid
            ):
                raise RuntimeError("image attempt is not reserved by this conversation")
            usage_date = self._today().isoformat()
            if provider == "nai_v5":
                await self._repository.increment_nai_v5_success(
                    conversation_key, member_openid, usage_date,
                    count_toward_shared=member_openid not in self._nai_v5_whitelist,
                )
            else:
                await self._repository.increment_success(conversation_key, usage_date)

    async def finish_attempt(self, conversation_key: str) -> None:
        async with self._lock:
            if self._active_conversation_key == conversation_key:
                self._active_conversation_key = None
                self._active_provider = ""
                self._active_member_openid = ""
