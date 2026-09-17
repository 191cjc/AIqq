import asyncio
import os
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

import aiosqlite

from ai_service import env_int


MAX_IMAGE_PROMPT_CHARS = 2000
IMAGE_CONTINUATION_TTL_SECONDS = 15 * 60


@dataclass(frozen=True)
class UsageDecision:
    allowed: bool
    message: str = ""


class RecentImageRequestStore:
    """Tracks short-lived chat image context without persisting conversations."""

    def __init__(
        self,
        ttl_seconds: int = IMAGE_CONTINUATION_TTL_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._expires_at: dict[str, float] = {}

    def has_context(self, conversation_key: str) -> bool:
        expires_at = self._expires_at.get(conversation_key)
        if expires_at is None:
            return False
        if expires_at <= self._clock():
            self._expires_at.pop(conversation_key, None)
            return False
        return True

    def mark(self, conversation_key: str) -> None:
        self._expires_at[conversation_key] = self._clock() + self.ttl_seconds

    def clear(self) -> None:
        self._expires_at.clear()

class ImageUsageStore:
    def __init__(
        self,
        db_path: str,
        *,
        daily_limit: int,
    ):
        self.db_path = db_path
        self.daily_limit = daily_limit
        self._active_conversation_key: str | None = None
        self._lock = asyncio.Lock()

    @classmethod
    def from_env(cls) -> "ImageUsageStore":
        limit_name = (
            "AIQQ_IMAGE_USER_DAILY_LIMIT"
            if os.getenv("AIQQ_IMAGE_USER_DAILY_LIMIT", "").strip()
            else "AIQQ_NOVELAI_USER_DAILY_LIMIT"
        )
        return cls(
            os.getenv(
                "AIQQ_STATE_DB",
                os.getenv("AIQQ_MEMORY_DB", "/var/lib/aiqq/state.db"),
            ).strip(),
            daily_limit=env_int(limit_name, 100, 1, 100),
        )

    async def initialize(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path, timeout=5) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS image_generation_usage (
                    conversation_key TEXT NOT NULL,
                    usage_date TEXT NOT NULL,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (conversation_key, usage_date)
                )
                """
            )
            await db.commit()

    async def reserve_attempt(self, conversation_key: str) -> UsageDecision:
        async with self._lock:
            if self._active_conversation_key is not None:
                return UsageDecision(
                    False,
                    "主人，上一张还在画，请等它完成或超时后再试喵。",
                )

            today = date.today().isoformat()
            async with aiosqlite.connect(self.db_path, timeout=5) as db:
                async with db.execute(
                    """
                    SELECT success_count
                    FROM image_generation_usage
                    WHERE conversation_key = ? AND usage_date = ?
                    """,
                    (conversation_key, today),
                ) as cursor:
                    row = await cursor.fetchone()
            if row and int(row[0]) >= self.daily_limit:
                return UsageDecision(
                    False,
                    f"主人，今天的生图额度用完啦（每日 {self.daily_limit} 张），明天再来喵。",
                )

            self._active_conversation_key = conversation_key
            return UsageDecision(True)

    async def finish_attempt(self, conversation_key: str) -> None:
        async with self._lock:
            if self._active_conversation_key == conversation_key:
                self._active_conversation_key = None

    async def record_success(self, conversation_key: str) -> None:
        today = date.today().isoformat()
        async with aiosqlite.connect(self.db_path, timeout=5) as db:
            await db.execute(
                """
                INSERT INTO image_generation_usage
                    (conversation_key, usage_date, success_count)
                VALUES (?, ?, 1)
                ON CONFLICT(conversation_key, usage_date) DO UPDATE SET
                    success_count = success_count + 1
                """,
                (conversation_key, today),
            )
            await db.commit()
