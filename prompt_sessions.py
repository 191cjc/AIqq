import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite


PROMPT_SESSION_TTL_SECONDS = 15 * 60
PROMPT_OPTION_COUNT = 3
MAX_PROMPT_OPTION_CHARS = 500


@dataclass(frozen=True)
class PromptSession:
    token: str
    prompts: tuple[str, ...]


class PromptSessionStore:
    def __init__(self, db_path: str):
        self.db_path = db_path

    @classmethod
    def from_env(cls) -> "PromptSessionStore":
        return cls(
            os.getenv("AIQQ_MEMORY_DB", "/var/lib/aiqq/memory.db").strip()
        )

    async def initialize(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS novelai_prompt_sessions (
                    token TEXT PRIMARY KEY,
                    conversation_key TEXT NOT NULL,
                    prompts_json TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_novelai_prompt_sessions_expiry
                ON novelai_prompt_sessions (expires_at)
                """
            )
            await db.commit()

    async def create(
        self, conversation_key: str, prompts: tuple[str, ...]
    ) -> PromptSession:
        normalized = tuple(
            " ".join(prompt.split()).strip()[:MAX_PROMPT_OPTION_CHARS]
            for prompt in prompts[:PROMPT_OPTION_COUNT]
            if prompt.strip()
        )
        if len(normalized) != PROMPT_OPTION_COUNT:
            raise ValueError("exactly three prompt options are required")

        token = secrets.token_urlsafe(12)
        now = int(time.time())
        expires_at = now + PROMPT_SESSION_TTL_SECONDS
        async with self._connect() as db:
            await db.execute(
                "DELETE FROM novelai_prompt_sessions WHERE expires_at <= ?",
                (now,),
            )
            await db.execute(
                """
                INSERT INTO novelai_prompt_sessions
                    (token, conversation_key, prompts_json, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    token,
                    conversation_key,
                    json.dumps(normalized, ensure_ascii=False),
                    expires_at,
                ),
            )
            await db.commit()
        return PromptSession(token, normalized)

    async def load(
        self, conversation_key: str, token: str
    ) -> PromptSession | None:
        if not token or len(token) > 64:
            return None
        now = int(time.time())
        async with self._connect() as db:
            async with db.execute(
                """
                SELECT prompts_json
                FROM novelai_prompt_sessions
                WHERE token = ? AND conversation_key = ? AND expires_at > ?
                """,
                (token, conversation_key, now),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return None
        if (
            not isinstance(value, list)
            or len(value) != PROMPT_OPTION_COUNT
            or not all(isinstance(prompt, str) and prompt for prompt in value)
        ):
            return None
        return PromptSession(token, tuple(value))

    def _connect(self):
        return aiosqlite.connect(self.db_path, timeout=5)
