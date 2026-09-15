"""Group-message persistence and context-specific queries."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from aiqq.logic.models import GroupHistoryMessage, GroupReplyContext

from .connection import SQLiteConnection
from .migrations import migrate_group_messages
from .models import GroupMessageSummary, StoredGroupMessage


GATEWAY_GROUP_EVENT_TYPES = {
    "GROUP_AT_MESSAGE_CREATE",
    "GROUP_MESSAGE_CREATE",
}
REFERENCE_EVENT_TYPES = (
    "GROUP_AT_MESSAGE_CREATE",
    "GROUP_MESSAGE_CREATE",
    "BOT_MESSAGE_CREATE",
)


class GroupMessageRepository:
    def __init__(self, connection: SQLiteConnection) -> None:
        self._database = connection
        self._migrated = False

    @property
    def is_open(self) -> bool:
        return self._database.is_open

    async def initialize(self) -> None:
        if self._migrated:
            return
        async with self._database.transaction() as connection:
            await migrate_group_messages(connection)
        self._migrated = True

    async def close(self) -> None:
        await self._database.close()
        self._migrated = False

    async def add_gateway_event(
        self, event_type: str, gateway_payload: dict[str, Any]
    ) -> bool:
        normalized_event = str(event_type or "").upper()
        if normalized_event not in GATEWAY_GROUP_EVENT_TYPES:
            return False
        if not isinstance(gateway_payload, dict):
            return False
        data = gateway_payload.get("d")
        if not isinstance(data, dict):
            return False
        message_id = _string(data.get("id"))
        group_openid = _string(data.get("group_openid"))
        if not message_id or not group_openid:
            return False
        author = data.get("author")
        if not isinstance(author, dict):
            author = {}
        message_type = data.get("message_type")
        if isinstance(message_type, bool) or not isinstance(message_type, int):
            message_type = None
        await self._upsert(
            (
                message_id,
                _string(gateway_payload.get("id")),
                normalized_event,
                group_openid,
                _string(author.get("member_openid") or author.get("id")),
                _string(author.get("username")),
                _string(author.get("member_role")),
                int(author.get("bot") is True),
                _string(data.get("content")),
                message_type,
                _string(data.get("timestamp")),
                _now(),
                _encode_payload(data),
                "",
            )
        )
        return True

    async def add_bot_message(
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
        progress: bool = False,
    ) -> bool:
        if not message_id or not group_openid or not isinstance(payload, dict):
            return False
        event_type = "BOT_PROGRESS_MESSAGE" if progress else "BOT_MESSAGE_CREATE"
        await self._upsert(
            (
                message_id,
                source_message_id,
                event_type,
                group_openid,
                "",
                username,
                "bot",
                1,
                content,
                message_type,
                sent_at,
                _now(),
                _encode_payload(payload),
                "",
            )
        )
        return True

    async def mark_recalled(
        self, *, group_id: str, message_id: str, recalled_at: str | None = None
    ) -> bool:
        await self.initialize()
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                """
                UPDATE group_messages
                SET recalled_at = ?
                WHERE group_openid = ? AND message_id = ? AND is_bot = 1
                """,
                (recalled_at or _now(), group_id, message_id),
            )
            return cursor.rowcount == 1

    async def is_owned_bot_message(
        self, *, group_id: str, message_id: str
    ) -> bool:
        await self.initialize()
        async with self._database.read() as connection:
            async with connection.execute(
                """
                SELECT 1 FROM group_messages
                WHERE group_openid = ? AND message_id = ? AND is_bot = 1
                  AND recalled_at = ''
                LIMIT 1
                """,
                (group_id, message_id),
            ) as cursor:
                return await cursor.fetchone() is not None

    async def list_for_reference(
        self,
        group_id: str,
        *,
        before_message_id: str,
        limit: int,
    ) -> tuple[GroupHistoryMessage, ...]:
        if not group_id:
            return ()
        await self.initialize()
        safe_limit = max(1, min(int(limit), 50))
        placeholders = ",".join("?" for _ in REFERENCE_EVENT_TYPES)
        params: list[Any] = [group_id, *REFERENCE_EVENT_TYPES]
        before_clause = ""
        if before_message_id:
            before_clause = (
                " AND record_id < COALESCE((SELECT record_id FROM group_messages "
                "WHERE group_openid = ? AND message_id = ? LIMIT 1), "
                "9223372036854775807)"
            )
            params.extend((group_id, before_message_id))
        params.append(safe_limit)
        async with self._database.read() as connection:
            async with connection.execute(
                f"""
                SELECT * FROM group_messages
                WHERE group_openid = ?
                  AND event_type IN ({placeholders})
                  AND recalled_at = ''
                  {before_clause}
                ORDER BY record_id DESC
                LIMIT ?
                """,
                params,
            ) as cursor:
                rows = await cursor.fetchall()
        records = [_row_to_message(row) for row in reversed(rows)]
        return tuple(_to_history_message(record) for record in records)

    async def find_console_reply_context(
        self,
        group_id: str,
        *,
        message_id: str | None = None,
    ) -> GroupReplyContext | None:
        if not group_id:
            return None
        await self.initialize()
        clauses = [
            "group_openid = ?",
            "is_bot = 0",
            "event_type IN ('GROUP_AT_MESSAGE_CREATE', 'GROUP_MESSAGE_CREATE')",
            "recalled_at = ''",
        ]
        params: list[Any] = [group_id]
        if message_id:
            clauses.append("message_id = ?")
            params.append(message_id)
        async with self._database.read() as connection:
            async with connection.execute(
                f"SELECT * FROM group_messages WHERE {' AND '.join(clauses)} "
                "ORDER BY record_id DESC LIMIT 100",
                params,
            ) as cursor:
                rows = await cursor.fetchall()
            now = datetime.now(timezone.utc)
            for row in rows:
                candidate = _row_to_message(row)
                if not _explicitly_mentions_bot(candidate):
                    continue
                sent_at = _parse_datetime(candidate.sent_at)
                if sent_at is None:
                    continue
                expires_at = sent_at + timedelta(minutes=5)
                if not sent_at - timedelta(seconds=30) <= now < expires_at:
                    continue
                used_sequences = await _used_reply_sequences(
                    connection, group_id, candidate.message_id
                )
                next_sequence = next(
                    (value for value in range(1, 6) if value not in used_sequences),
                    None,
                )
                if next_sequence is not None:
                    return GroupReplyContext(
                        group_id=group_id,
                        message_id=candidate.message_id,
                        next_sequence=next_sequence,
                        expires_at=expires_at,
                    )
        return None

    async def recent(
        self,
        group_id: str,
        *,
        limit: int = 20,
        before_record_id: int | None = None,
        exclude_message_id: str = "",
    ) -> tuple[StoredGroupMessage, ...]:
        if not group_id:
            return ()
        await self.initialize()
        safe_limit = max(1, min(int(limit), 100))
        clauses = ["group_openid = ?"]
        params: list[Any] = [group_id]
        if before_record_id is not None:
            clauses.append("record_id < ?")
            params.append(max(1, int(before_record_id)))
        if exclude_message_id:
            clauses.append("message_id <> ?")
            params.append(exclude_message_id)
        params.append(safe_limit)
        async with self._database.read() as connection:
            async with connection.execute(
                f"SELECT * FROM group_messages WHERE {' AND '.join(clauses)} "
                "ORDER BY record_id DESC LIMIT ?",
                params,
            ) as cursor:
                rows = await cursor.fetchall()
        return tuple(_row_to_message(row) for row in rows)

    async def groups(
        self, *, limit: int = 200
    ) -> tuple[GroupMessageSummary, ...]:
        await self.initialize()
        safe_limit = max(1, min(int(limit), 500))
        async with self._database.read() as connection:
            async with connection.execute(
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

    async def get_by_record_id(
        self, group_id: str, record_id: int
    ) -> StoredGroupMessage | None:
        if not group_id or record_id < 1:
            return None
        await self.initialize()
        async with self._database.read() as connection:
            async with connection.execute(
                """
                SELECT * FROM group_messages
                WHERE group_openid = ? AND record_id = ? AND recalled_at = ''
                LIMIT 1
                """,
                (group_id, record_id),
            ) as cursor:
                row = await cursor.fetchone()
        return _row_to_message(row) if row is not None else None

    async def list_image_urls(
        self, group_id: str, record_id: int
    ) -> tuple[str, ...]:
        record = await self.get_by_record_id(group_id, record_id)
        if record is None:
            return ()
        return _extract_image_urls(record.payload)

    async def _upsert(self, values: tuple[Any, ...]) -> None:
        await self.initialize()
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO group_messages (
                    message_id, event_id, event_type, group_openid, member_openid,
                    username, member_role, is_bot, content, message_type, sent_at,
                    received_at, payload_json, recalled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    event_id=excluded.event_id,
                    event_type=excluded.event_type,
                    group_openid=excluded.group_openid,
                    member_openid=excluded.member_openid,
                    username=excluded.username,
                    member_role=excluded.member_role,
                    is_bot=excluded.is_bot,
                    content=excluded.content,
                    message_type=excluded.message_type,
                    sent_at=excluded.sent_at,
                    received_at=excluded.received_at,
                    payload_json=excluded.payload_json,
                    recalled_at=excluded.recalled_at
                """,
                values,
            )


def _to_history_message(record: StoredGroupMessage) -> GroupHistoryMessage:
    return GroupHistoryMessage(
        record_id=record.record_id,
        role="assistant" if record.is_bot else "user",
        sender_name=record.username or ("AiQQ" if record.is_bot else "群成员"),
        sent_at=record.sent_at or record.received_at,
        content=record.content,
        message_type=record.message_type,
        reply_summary=_extract_reply_summary(record.payload),
        has_image=_has_image(record.payload),
    )


def _extract_reply_summary(payload: dict[str, Any]) -> str:
    summaries: list[str] = []
    pending = payload.get("msg_elements")
    queue = list(pending) if isinstance(pending, list) else []
    while queue and len(summaries) < 3:
        element = queue.pop(0)
        if not isinstance(element, dict):
            continue
        content = element.get("content")
        if isinstance(content, str) and content.strip():
            summaries.append(content.strip())
        nested = element.get("msg_elements")
        if isinstance(nested, list):
            queue.extend(nested)
    return " ".join(summaries)


def _has_image(payload: dict[str, Any]) -> bool:
    pending: list[Any] = [payload]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            content_type = value.get("content_type")
            if isinstance(content_type, str) and content_type.lower().startswith("image/"):
                return True
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return False


def _extract_image_urls(payload: dict[str, Any]) -> tuple[str, ...]:
    urls: list[str] = []
    seen: set[str] = set()
    pending: list[Any] = [payload]
    while pending and len(urls) < 10:
        value = pending.pop(0)
        if isinstance(value, dict):
            content_type = value.get("content_type")
            url = value.get("url")
            if (
                isinstance(content_type, str)
                and content_type.lower().startswith("image/")
                and isinstance(url, str)
                and url
                and url not in seen
            ):
                seen.add(url)
                urls.append(url)
            for key in ("attachments", "msg_elements"):
                nested = value.get(key)
                if isinstance(nested, list):
                    pending.extend(nested)
        elif isinstance(value, list):
            pending.extend(value)
    return tuple(urls)


def _explicitly_mentions_bot(record: StoredGroupMessage) -> bool:
    if record.event_type == "GROUP_AT_MESSAGE_CREATE":
        return True
    mentions = record.payload.get("mentions")
    return isinstance(mentions, list) and any(
        isinstance(mention, dict) and mention.get("is_you") is True
        for mention in mentions
    )


async def _used_reply_sequences(
    connection: aiosqlite.Connection, group_id: str, source_message_id: str
) -> set[int]:
    async with connection.execute(
        """
        SELECT payload_json FROM group_messages
        WHERE group_openid = ? AND event_id = ? AND is_bot = 1
        """,
        (group_id, source_message_id),
    ) as cursor:
        rows = await cursor.fetchall()
    used: set[int] = set()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        sequence = payload.get("msg_seq") if isinstance(payload, dict) else None
        if isinstance(sequence, int) and not isinstance(sequence, bool):
            used.add(sequence)
    return used


def _row_to_message(row: aiosqlite.Row) -> StoredGroupMessage:
    try:
        payload = json.loads(row["payload_json"])
    except (json.JSONDecodeError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    keys = set(row.keys())
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
        recalled_at=row["recalled_at"] if "recalled_at" in keys else "",
    )


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _encode_payload(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
