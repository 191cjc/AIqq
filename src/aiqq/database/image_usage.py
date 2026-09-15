"""Persistent successful-image counters stored in the existing state database."""

from __future__ import annotations

from .connection import SQLiteConnection


class ImageUsageRepository:
    def __init__(self, connection: SQLiteConnection) -> None:
        self._connection = connection

    async def initialize(self) -> None:
        async with self._connection.transaction() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS image_generation_usage (
                    conversation_key TEXT NOT NULL,
                    usage_date TEXT NOT NULL,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (conversation_key, usage_date)
                )
                """
            )

    async def success_count(self, conversation_key: str, usage_date: str) -> int:
        async with self._connection.read() as connection:
            async with connection.execute(
                """
                SELECT success_count
                FROM image_generation_usage
                WHERE conversation_key = ? AND usage_date = ?
                """,
                (conversation_key, usage_date),
            ) as cursor:
                row = await cursor.fetchone()
        return int(row[0]) if row is not None else 0

    async def increment_success(self, conversation_key: str, usage_date: str) -> None:
        async with self._connection.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO image_generation_usage
                    (conversation_key, usage_date, success_count)
                VALUES (?, ?, 1)
                ON CONFLICT(conversation_key, usage_date) DO UPDATE SET
                    success_count = success_count + 1
                """,
                (conversation_key, usage_date),
            )
