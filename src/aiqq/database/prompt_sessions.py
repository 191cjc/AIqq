"""NovelAI prompt-edit sessions stored in the existing state database."""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable

from aiqq.logic.models import NovelAIPromptOptions, NovelAIPromptSession

from .connection import SQLiteConnection


PROMPT_SESSION_TTL_SECONDS = 15 * 60


class NovelAIPromptSessionRepository:
    def __init__(
        self,
        connection: SQLiteConnection,
        *,
        ttl_seconds: int = PROMPT_SESSION_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        if ttl_seconds < 1:
            raise ValueError("prompt session TTL must be positive")
        self._connection = connection
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(12))

    async def initialize(self) -> None:
        async with self._connection.transaction() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS novelai_prompt_sessions (
                    token TEXT PRIMARY KEY,
                    conversation_key TEXT NOT NULL,
                    prompts_json TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                )
                """
            )
            await connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_novelai_prompt_sessions_expiry
                ON novelai_prompt_sessions (expires_at)
                """
            )

    async def create(
        self, conversation_key: str, options: NovelAIPromptOptions
    ) -> NovelAIPromptSession:
        if not conversation_key:
            raise ValueError("conversation key must not be empty")
        token = self._token_factory()
        if not token or len(token) > 64:
            raise ValueError("prompt session token factory returned an invalid token")
        now = int(self._clock())
        async with self._connection.transaction() as connection:
            await connection.execute(
                "DELETE FROM novelai_prompt_sessions WHERE expires_at <= ?",
                (now,),
            )
            await connection.execute(
                """
                INSERT INTO novelai_prompt_sessions
                    (token, conversation_key, prompts_json, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    token,
                    conversation_key,
                    json.dumps(options.prompts, ensure_ascii=False),
                    now + self._ttl_seconds,
                ),
            )
        return NovelAIPromptSession(token, options)

    async def load(
        self, conversation_key: str, token: str
    ) -> NovelAIPromptSession | None:
        if not conversation_key or not token or len(token) > 64:
            return None
        now = int(self._clock())
        async with self._connection.read() as connection:
            async with connection.execute(
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
            raw_prompts = json.loads(row[0])
            if not isinstance(raw_prompts, list) or len(raw_prompts) != 3:
                return None
            options = NovelAIPromptOptions(tuple(raw_prompts))  # type: ignore[arg-type]
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        return NovelAIPromptSession(token, options)
