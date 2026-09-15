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
        today: Callable[[], date] = date.today,
    ) -> None:
        if daily_limit < 1:
            raise ValueError("daily image limit must be positive")
        self._repository = repository
        self._daily_limit = daily_limit
        self._today = today
        self._active_conversation_key: str | None = None
        self._lock = asyncio.Lock()

    async def reserve_attempt(self, conversation_key: str) -> ImageUsageDecision:
        if not conversation_key:
            raise ValueError("conversation key must not be empty")
        async with self._lock:
            if self._active_conversation_key is not None:
                return ImageUsageDecision(False, "busy")
            usage_date = self._today().isoformat()
            success_count = await self._repository.success_count(
                conversation_key, usage_date
            )
            if success_count >= self._daily_limit:
                return ImageUsageDecision(
                    False, "daily_limit", daily_limit=self._daily_limit
                )
            self._active_conversation_key = conversation_key
            return ImageUsageDecision(True)

    async def record_success(self, conversation_key: str) -> None:
        async with self._lock:
            if self._active_conversation_key != conversation_key:
                raise RuntimeError("image attempt is not reserved by this conversation")
            await self._repository.increment_success(
                conversation_key, self._today().isoformat()
            )

    async def finish_attempt(self, conversation_key: str) -> None:
        async with self._lock:
            if self._active_conversation_key == conversation_key:
                self._active_conversation_key = None
