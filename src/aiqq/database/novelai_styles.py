"""Persistent, provider-specific NovelAI style preferences owned by QQ senders."""

from __future__ import annotations

from typing import Literal

from .connection import SQLiteConnection


class NovelAIStyleRepository:
    def __init__(
        self, connection: SQLiteConnection,
        *, provider: Literal["novelai", "nai_v5"] = "novelai",
    ) -> None:
        tables = {
            "novelai": "novelai_user_styles",
            "nai_v5": "nai_v5_user_styles",
        }
        if provider not in tables:
            raise ValueError("unsupported style provider")
        self._connection = connection
        self._table = tables[provider]

    async def initialize(self) -> None:
        async with self._connection.transaction() as connection:
            await connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._table} (
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
                f"SELECT prompt FROM {self._table} WHERE member_openid = ?",
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
                    f"""
                    INSERT INTO {self._table} (member_openid, prompt)
                    VALUES (?, ?)
                    ON CONFLICT(member_openid) DO UPDATE SET prompt = excluded.prompt
                    """,
                    (member_openid, normalized),
                )
            else:
                await connection.execute(
                    f"DELETE FROM {self._table} WHERE member_openid = ?",
                    (member_openid,),
                )
