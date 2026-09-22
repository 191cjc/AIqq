"""Persistent NovelAI style preferences owned by QQ senders."""

from __future__ import annotations

from .connection import SQLiteConnection


class NovelAIStyleRepository:
    def __init__(self, connection: SQLiteConnection) -> None:
        self._connection = connection

    async def initialize(self) -> None:
        async with self._connection.transaction() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS novelai_user_styles (
                    member_openid TEXT PRIMARY KEY,
                    prompt TEXT NOT NULL
                )
                """
            )

    async def load(self, member_openid: str) -> str | None:
        if not member_openid.strip():
            raise ValueError("member openid must not be empty")
        async with self._connection.read() as connection:
            async with connection.execute(
                "SELECT prompt FROM novelai_user_styles WHERE member_openid = ?",
                (member_openid,),
            ) as cursor:
                row = await cursor.fetchone()
        return row[0] if row is not None else None

    async def save(self, member_openid: str, prompt: str) -> None:
        if not member_openid.strip():
            raise ValueError("member openid must not be empty")
        normalized = prompt.strip()
        async with self._connection.transaction() as connection:
            if normalized:
                await connection.execute(
                    """
                    INSERT INTO novelai_user_styles (member_openid, prompt)
                    VALUES (?, ?)
                    ON CONFLICT(member_openid) DO UPDATE SET prompt = excluded.prompt
                    """,
                    (member_openid, normalized),
                )
            else:
                await connection.execute(
                    "DELETE FROM novelai_user_styles WHERE member_openid = ?",
                    (member_openid,),
                )
