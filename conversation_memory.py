import asyncio
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from ai_service import AIResult, AIService, StageCallback, env_int


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredMessage:
    id: int
    role: str
    content: str

    def as_model_input(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class ConversationContext:
    summary: str
    messages: tuple[StoredMessage, ...]

    def model_history(self) -> list[dict[str, str]]:
        return [message.as_model_input() for message in self.messages]


class ConversationMemory:
    def __init__(self, db_path: str, summary_every_rounds: int = 10):
        self.db_path = db_path
        self.summary_every_rounds = summary_every_rounds

    @classmethod
    def from_env(cls) -> "ConversationMemory":
        return cls(
            os.getenv("AIQQ_MEMORY_DB", "/var/lib/aiqq/memory.db").strip(),
            env_int("AIQQ_SUMMARY_EVERY_ROUNDS", 10, 2, 100),
        )

    async def initialize(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_key TEXT PRIMARY KEY,
                    summary TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_key TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_conversation_messages_key_id
                ON conversation_messages (conversation_key, id);
                """
            )
            await db.commit()

    async def load_context(self, conversation_key: str) -> ConversationContext:
        async with self._connect() as db:
            async with db.execute(
                "SELECT summary FROM conversations WHERE conversation_key = ?",
                (conversation_key,),
            ) as cursor:
                row = await cursor.fetchone()
            summary = row[0] if row else ""

            async with db.execute(
                """
                SELECT id, role, content
                FROM conversation_messages
                WHERE conversation_key = ?
                ORDER BY id
                """,
                (conversation_key,),
            ) as cursor:
                rows = await cursor.fetchall()

        return ConversationContext(
            summary,
            tuple(StoredMessage(int(row[0]), row[1], row[2]) for row in rows),
        )

    async def append_round(
        self, conversation_key: str, user_content: str, assistant_content: str
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO conversations (conversation_key)
                VALUES (?)
                ON CONFLICT(conversation_key) DO UPDATE SET
                    updated_at = CURRENT_TIMESTAMP
                """,
                (conversation_key,),
            )
            await db.executemany(
                """
                INSERT INTO conversation_messages
                    (conversation_key, role, content)
                VALUES (?, ?, ?)
                """,
                (
                    (conversation_key, "user", user_content),
                    (conversation_key, "assistant", assistant_content),
                ),
            )
            await db.commit()

    async def get_summary_batch(
        self, conversation_key: str
    ) -> tuple[str, tuple[StoredMessage, ...]] | None:
        context = await self.load_context(conversation_key)
        batch_size = self.summary_every_rounds * 2
        if len(context.messages) < batch_size:
            return None
        return context.summary, context.messages[:batch_size]

    async def replace_batch_with_summary(
        self,
        conversation_key: str,
        batch: Sequence[StoredMessage],
        summary: str,
    ) -> None:
        if not batch:
            return
        last_message_id = batch[-1].id
        async with self._connect() as db:
            await db.execute(
                """
                UPDATE conversations
                SET summary = ?, updated_at = CURRENT_TIMESTAMP
                WHERE conversation_key = ?
                """,
                (summary, conversation_key),
            )
            await db.execute(
                """
                DELETE FROM conversation_messages
                WHERE conversation_key = ? AND id <= ?
                """,
                (conversation_key, last_message_id),
            )
            await db.commit()

    async def clear(self, conversation_key: str) -> None:
        async with self._connect() as db:
            await db.execute(
                "DELETE FROM conversation_messages WHERE conversation_key = ?",
                (conversation_key,),
            )
            await db.execute(
                "DELETE FROM conversations WHERE conversation_key = ?",
                (conversation_key,),
            )
            await db.commit()

    def _connect(self):
        return aiosqlite.connect(self.db_path, timeout=5)


class ConversationManager:
    def __init__(self, ai: AIService, memory: ConversationMemory):
        self.ai = ai
        self.memory = memory
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def chat(
        self,
        conversation_key: str,
        prompt: str,
        *,
        on_stage: StageCallback | None = None,
    ) -> AIResult:
        lock = await self._get_lock(conversation_key)
        async with lock:
            context = await self.memory.load_context(conversation_key)
            result = await self.ai.answer(
                prompt,
                history=context.model_history(),
                summary=context.summary,
                on_stage=on_stage,
            )
            if result.success:
                await self.memory.append_round(conversation_key, prompt, result.text)
            return result

    async def summarize_if_needed(self, conversation_key: str) -> bool:
        lock = await self._get_lock(conversation_key)
        async with lock:
            summary_batch = await self.memory.get_summary_batch(conversation_key)
            if summary_batch is None:
                return False

            existing_summary, messages = summary_batch
            result = await self.ai.summarize(
                existing_summary,
                [message.as_model_input() for message in messages],
            )
            if not result.success:
                logger.warning("对话摘要生成失败，将保留原始上下文稍后重试")
                return False

            await self.memory.replace_batch_with_summary(
                conversation_key, messages, result.text
            )
            logger.info(
                "已将对话的 %s 轮消息压缩为摘要", self.memory.summary_every_rounds
            )
            return True

    async def clear(self, conversation_key: str) -> None:
        lock = await self._get_lock(conversation_key)
        async with lock:
            await self.memory.clear(conversation_key)

    async def load_context(self, conversation_key: str) -> ConversationContext:
        lock = await self._get_lock(conversation_key)
        async with lock:
            return await self.memory.load_context(conversation_key)

    async def record_round(
        self, conversation_key: str, user_content: str, assistant_content: str
    ) -> None:
        lock = await self._get_lock(conversation_key)
        async with lock:
            await self.memory.append_round(
                conversation_key, user_content, assistant_content
            )

    async def _get_lock(self, conversation_key: str) -> asyncio.Lock:
        async with self._locks_guard:
            return self._locks.setdefault(conversation_key, asyncio.Lock())
