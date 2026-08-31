import asyncio
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import aiosqlite

from ai_service import env_int


MAX_IMAGE_PROMPT_CHARS = 800


@dataclass(frozen=True)
class UsageDecision:
    allowed: bool
    message: str = ""


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
        return cls(
            os.getenv("AIQQ_MEMORY_DB", "/var/lib/aiqq/memory.db").strip(),
            daily_limit=env_int("AIQQ_NOVELAI_USER_DAILY_LIMIT", 100, 1, 100),
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
                    "当前已有图片正在生成，请等待它完成或超时后再试。",
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
                    f"你今天的 NovelAI 生图额度已用完（每日 {self.daily_limit} 张）。",
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
