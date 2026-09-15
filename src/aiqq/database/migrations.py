"""Explicit database migrations for the existing AiQQ files."""

from __future__ import annotations

import aiosqlite


async def migrate_group_messages(connection: aiosqlite.Connection) -> None:
    await connection.execute(
        """
        CREATE TABLE IF NOT EXISTS group_messages (
            record_id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT NOT NULL UNIQUE,
            event_id TEXT NOT NULL DEFAULT '',
            event_type TEXT NOT NULL,
            group_openid TEXT NOT NULL,
            member_openid TEXT NOT NULL DEFAULT '',
            username TEXT NOT NULL DEFAULT '',
            member_role TEXT NOT NULL DEFAULT '',
            is_bot INTEGER NOT NULL DEFAULT 0,
            content TEXT NOT NULL DEFAULT '',
            message_type INTEGER,
            sent_at TEXT NOT NULL DEFAULT '',
            received_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            recalled_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    async with connection.execute("PRAGMA table_info(group_messages)") as cursor:
        columns = {row[1] for row in await cursor.fetchall()}
    if "recalled_at" not in columns:
        await connection.execute(
            "ALTER TABLE group_messages "
            "ADD COLUMN recalled_at TEXT NOT NULL DEFAULT ''"
        )
    await connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_group_messages_group_record
        ON group_messages (group_openid, record_id DESC)
        """
    )
    await connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_group_messages_member_record
        ON group_messages (group_openid, member_openid, record_id DESC)
        """
    )
    await connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_group_messages_reference
        ON group_messages (group_openid, event_type, recalled_at, record_id DESC)
        """
    )
