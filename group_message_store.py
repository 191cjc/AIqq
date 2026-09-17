import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite


GROUP_MESSAGE_EVENT_TYPES = {
    "GROUP_AT_MESSAGE_CREATE",
    "GROUP_MESSAGE_CREATE",
}
DEFAULT_GROUP_MESSAGE_DB = "/var/lib/aiqq/group_messages.db"


@dataclass(frozen=True)
class StoredGroupMessage:
    record_id: int
    message_id: str
    event_id: str
    event_type: str
    group_openid: str
    member_openid: str
    username: str
    member_role: str
    is_bot: bool
    content: str
    message_type: int | None
    sent_at: str
    received_at: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class GroupMessageSummary:
    group_openid: str
    message_count: int
    latest_record_id: int
    latest_member_openid: str
    latest_username: str
    latest_content: str
    latest_sent_at: str


class GroupMessageStore:
    """Persists QQ group message events for later contextual lookup."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @classmethod
    def from_env(cls) -> "GroupMessageStore":
        return cls(
            os.getenv("AIQQ_GROUP_MESSAGE_DB", DEFAULT_GROUP_MESSAGE_DB).strip()
            or DEFAULT_GROUP_MESSAGE_DB
        )

    @property
    def is_open(self) -> bool:
        return self._db is not None

    async def initialize(self) -> None:
        async with self._lock:
            await self._initialize_locked()

    async def _initialize_locked(self) -> None:
        if self._db is not None:
            return
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self.db_path, timeout=10)
        try:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.execute("PRAGMA busy_timeout=10000")
            await db.execute(
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
                    payload_json TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_group_messages_group_record
                ON group_messages (group_openid, record_id DESC)
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_group_messages_member_record
                ON group_messages (group_openid, member_openid, record_id DESC)
                """
            )
            await db.commit()
        except Exception:
            await db.close()
            raise
        self._db = db
        os.chmod(self.db_path, 0o600)

    async def save_gateway_event(
        self,
        event_type: str,
        gateway_payload: dict[str, Any],
    ) -> bool:
        normalized_event = str(event_type or "").upper()
        if normalized_event not in GROUP_MESSAGE_EVENT_TYPES:
            return False
        if not isinstance(gateway_payload, dict):
            return False
        data = gateway_payload.get("d")
        if not isinstance(data, dict):
            return False

        message_id = self._string(data.get("id"))
        group_openid = self._string(data.get("group_openid"))
        if not message_id or not group_openid:
            return False

        author = data.get("author")
        if not isinstance(author, dict):
            author = {}
        message_type = data.get("message_type")
        if isinstance(message_type, bool) or not isinstance(message_type, int):
            message_type = None

        payload_json = json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        received_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        values = (
            message_id,
            self._string(gateway_payload.get("id")),
            normalized_event,
            group_openid,
            self._string(author.get("member_openid") or author.get("id")),
            self._string(author.get("username")),
            self._string(author.get("member_role")),
            int(author.get("bot") is True),
            self._string(data.get("content")),
            message_type,
            self._string(data.get("timestamp")),
            received_at,
            payload_json,
        )

        await self._upsert(values)
        return True

    async def save_bot_reply(
        self,
        *,
        message_id: str,
        group_openid: str,
        username: str,
        content: str,
        message_type: int,
        sent_at: str,
        payload: dict[str, Any],
        source_message_id: str = "",
    ) -> bool:
        if not message_id or not group_openid or not isinstance(payload, dict):
            return False
        received_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        payload_json = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        values = (
            message_id,
            source_message_id,
            "BOT_MESSAGE_CREATE",
            group_openid,
            "",
            username,
            "bot",
            1,
            content,
            message_type,
            sent_at,
            received_at,
            payload_json,
        )
        await self._upsert(values)
        return True

    async def _upsert(self, values: tuple[Any, ...]) -> None:
        async with self._lock:
            await self._initialize_locked()
            assert self._db is not None
            await self._db.execute(
                """
                INSERT INTO group_messages (
                    message_id,
                    event_id,
                    event_type,
                    group_openid,
                    member_openid,
                    username,
                    member_role,
                    is_bot,
                    content,
                    message_type,
                    sent_at,
                    received_at,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    event_id = excluded.event_id,
                    event_type = excluded.event_type,
                    group_openid = excluded.group_openid,
                    member_openid = excluded.member_openid,
                    username = excluded.username,
                    member_role = excluded.member_role,
                    is_bot = excluded.is_bot,
                    content = excluded.content,
                    message_type = excluded.message_type,
                    sent_at = excluded.sent_at,
                    received_at = excluded.received_at,
                    payload_json = excluded.payload_json
                """,
                values,
            )
            await self._db.commit()

    async def recent(
        self,
        group_openid: str,
        *,
        limit: int = 20,
        before_record_id: int | None = None,
        exclude_message_id: str = "",
    ) -> tuple[StoredGroupMessage, ...]:
        if not group_openid:
            return ()
        safe_limit = max(1, min(int(limit), 100))
        params: list[Any] = [group_openid]
        before_clause = ""
        if before_record_id is not None:
            before_clause = " AND record_id < ?"
            params.append(max(1, int(before_record_id)))
        exclude_clause = ""
        if exclude_message_id:
            exclude_clause = " AND message_id <> ?"
            params.append(exclude_message_id)
        params.append(safe_limit)

        async with self._lock:
            await self._initialize_locked()
            assert self._db is not None
            self._db.row_factory = aiosqlite.Row
            async with self._db.execute(
                f"""
                SELECT *
                FROM group_messages
                WHERE group_openid = ?{before_clause}{exclude_clause}
                ORDER BY record_id DESC
                LIMIT ?
                """,
                params,
            ) as cursor:
                rows = await cursor.fetchall()
        return tuple(self._row_to_message(row) for row in rows)

    async def recent_images(
        self,
        group_openid: str,
        *,
        limit: int = 50,
        exclude_message_id: str = "",
    ) -> tuple[StoredGroupMessage, ...]:
        """Return recent records that contain at least one image attachment."""
        if not group_openid:
            return ()
        safe_limit = max(1, min(int(limit), 100))
        params: list[Any] = [group_openid]
        exclude_clause = ""
        if exclude_message_id:
            exclude_clause = " AND message_id <> ?"
            params.append(exclude_message_id)
        params.append(safe_limit)

        async with self._lock:
            await self._initialize_locked()
            assert self._db is not None
            self._db.row_factory = aiosqlite.Row
            async with self._db.execute(
                f"""
                SELECT *
                FROM group_messages
                WHERE group_openid = ?{exclude_clause}
                  AND payload_json LIKE '%"content_type":"image/%'
                ORDER BY record_id DESC
                LIMIT ?
                """,
                params,
            ) as cursor:
                rows = await cursor.fetchall()
        return tuple(self._row_to_message(row) for row in rows)

    async def get_message(
        self,
        group_openid: str,
        message_id: str,
    ) -> StoredGroupMessage | None:
        if not group_openid or not message_id:
            return None
        async with self._lock:
            await self._initialize_locked()
            assert self._db is not None
            self._db.row_factory = aiosqlite.Row
            async with self._db.execute(
                """
                SELECT *
                FROM group_messages
                WHERE group_openid = ? AND message_id = ?
                LIMIT 1
                """,
                (group_openid, message_id),
            ) as cursor:
                row = await cursor.fetchone()
        return self._row_to_message(row) if row is not None else None

    async def groups(
        self, *, limit: int = 200
    ) -> tuple[GroupMessageSummary, ...]:
        safe_limit = max(1, min(int(limit), 500))
        async with self._lock:
            await self._initialize_locked()
            assert self._db is not None
            self._db.row_factory = aiosqlite.Row
            async with self._db.execute(
                """
                WITH message_counts AS (
                    SELECT
                        group_openid,
                        COUNT(*) AS message_count,
                        MAX(record_id) AS latest_record_id
                    FROM group_messages
                    GROUP BY group_openid
                )
                SELECT
                    counts.group_openid,
                    counts.message_count,
                    counts.latest_record_id,
                    messages.member_openid AS latest_member_openid,
                    messages.username AS latest_username,
                    messages.content AS latest_content,
                    messages.sent_at AS latest_sent_at
                FROM message_counts AS counts
                JOIN group_messages AS messages
                    ON messages.record_id = counts.latest_record_id
                ORDER BY counts.latest_record_id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ) as cursor:
                rows = await cursor.fetchall()
        return tuple(
            GroupMessageSummary(
                group_openid=row["group_openid"],
                message_count=int(row["message_count"]),
                latest_record_id=int(row["latest_record_id"]),
                latest_member_openid=row["latest_member_openid"],
                latest_username=row["latest_username"],
                latest_content=row["latest_content"],
                latest_sent_at=row["latest_sent_at"],
            )
            for row in rows
        )

    async def count(self) -> int:
        async with self._lock:
            await self._initialize_locked()
            assert self._db is not None
            async with self._db.execute(
                "SELECT COUNT(*) FROM group_messages"
            ) as cursor:
                row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def close(self) -> None:
        async with self._lock:
            db = self._db
            self._db = None
            if db is not None:
                await db.close()

    @staticmethod
    def _string(value: Any) -> str:
        return value if isinstance(value, str) else ""

    @staticmethod
    def _row_to_message(row: aiosqlite.Row) -> StoredGroupMessage:
        try:
            payload = json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return StoredGroupMessage(
            record_id=int(row["record_id"]),
            message_id=row["message_id"],
            event_id=row["event_id"],
            event_type=row["event_type"],
            group_openid=row["group_openid"],
            member_openid=row["member_openid"],
            username=row["username"],
            member_role=row["member_role"],
            is_bot=bool(row["is_bot"]),
            content=row["content"],
            message_type=row["message_type"],
            sent_at=row["sent_at"],
            received_at=row["received_at"],
            payload=payload,
        )
