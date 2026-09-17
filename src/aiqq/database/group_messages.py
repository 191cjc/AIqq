"""Group-message persistence and context-specific queries."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from aiqq.logic.models import GroupHistoryMessage, GroupReplyContext

from .connection import SQLiteConnection
from .history import attachment_objects, decode_json, encode_json
from .message_events import MessageEventStore, LOCAL_RECALL_EVENT
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
        self._initialize_lock = asyncio.Lock()
        self._events = MessageEventStore(connection)

    @property
    def is_open(self) -> bool:
        return self._database.is_open

    async def initialize(self) -> None:
        if self._migrated:
            return
        async with self._initialize_lock:
            if self._migrated:
                return
            async with self._database.transaction() as connection:
                await migrate_group_messages(connection)
            await self._events.recover()
            self._migrated = True

    async def close(self) -> None:
        await self._database.close()
        self._migrated = False

    async def add_gateway_event(
        self, event_type: str, gateway_payload: dict[str, Any], *,
        raw_text: str | None = None, connection_id: str = "",
    ) -> bool:
        normalized_event = str(event_type or "").upper()
        if not isinstance(gateway_payload, dict):
            return False
        data = gateway_payload.get("d")
        if not normalized_event.startswith("GROUP_") and not (
            isinstance(data, dict) and isinstance(data.get("group_openid"), str)
        ):
            return False
        await self.initialize()
        return await self._events.add(
            normalized_event, gateway_payload, raw_text=raw_text, connection_id=connection_id,
        )

    async def recover_pending_events(self) -> dict[str, int]:
        await self.initialize()
        return await self._events.recover()

    async def storage_status(self) -> dict[str, int]:
        await self.initialize()
        async with self._database.read() as connection:
            counts = {}
            for table in ("group_message_events", "message_versions", "message_attachments"):
                async with connection.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
                    counts[table] = (await cursor.fetchone())[0]
            async with connection.execute(
                "SELECT COUNT(*) FROM message_event_processing WHERE status IN ('pending','failed')"
            ) as cursor:
                counts["unprojected_events"] = (await cursor.fetchone())[0]
        counts["write_failures"] = self._events.write_failures
        return counts

    async def add_bot_message(
        self, *, message_id: str, group_openid: str, username: str, content: str,
        message_type: int, sent_at: str, payload: dict[str, Any],
        source_message_id: str = "", progress: bool = False,
    ) -> bool:
        if not message_id or not group_openid or not isinstance(payload, dict):
            return False
        await self.initialize()
        event_type = "BOT_PROGRESS_MESSAGE" if progress else "BOT_MESSAGE_CREATE"
        envelope = {
            "t": event_type, "id": source_message_id, "d": payload,
            "record": {
                "message_id": message_id, "group_openid": group_openid,
                "username": username, "content": content, "message_type": message_type,
                "sent_at": sent_at,
            },
        }
        return await self._events.add(event_type, envelope, source="local_delivery")

    async def mark_recalled(
        self, *, group_id: str, message_id: str, recalled_at: str | None = None
    ) -> bool:
        if not group_id or not message_id:
            return False
        await self.initialize()
        return await self._events.add(
            LOCAL_RECALL_EVENT,
            {"t": LOCAL_RECALL_EVENT, "d": {
                "group_openid": group_id, "id": message_id,
                "recalled_at": recalled_at or _now(), "confirmation_source": "qq_api_success",
            }}, source="local_recall",
        )

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
        params: list[Any] = [group_id]
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
                  {before_clause}
                ORDER BY record_id DESC
                LIMIT ?
                """,
                params,
            ) as cursor:
                rows = await cursor.fetchall()
            records = [
                _row_to_message(row, await _wire_record(connection, row)) for row in reversed(rows)
            ]
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


    async def get_full_message(self, group_id: str, message_id: str) -> dict[str, Any] | None:
        if not group_id or not message_id:
            return None
        await self.initialize()
        async with self._database.read() as connection:
            row = await _fetch_one(connection,
                "SELECT * FROM group_messages WHERE group_openid=? AND message_id=?",
                (group_id, message_id))
            return await _wire_record(connection, row) if row is not None else None

    async def get_current_for_reference(
        self, group_id: str, message_id: str
    ) -> GroupHistoryMessage | None:
        wire = await self.get_full_message(group_id, message_id)
        if wire is None:
            return None
        return _to_history_message(_row_to_message(wire["record"], wire))

    async def has_before(self, group_id: str, record_id: int) -> bool:
        await self.initialize()
        async with self._database.read() as connection:
            return await _fetch_one(connection,
                "SELECT 1 FROM group_messages WHERE group_openid=? AND record_id<? LIMIT 1",
                (group_id, record_id)) is not None

    async def get_full_record(
        self, group_id: str, record_id: int, version_id: int | None = None
    ) -> dict[str, Any] | None:
        if not group_id or record_id < 1:
            return None
        await self.initialize()
        async with self._database.read() as connection:
            row = await _fetch_one(connection,
                "SELECT * FROM group_messages WHERE group_openid=? AND record_id=?",
                (group_id, record_id))
            if row is None:
                return None
            if version_id is None:
                return await _wire_record(connection, row)
            version = await _fetch_one(connection,
                "SELECT * FROM message_versions WHERE record_id=? AND version_id=?",
                (record_id, version_id))
            if version is None:
                return None
            snapshot = json.loads(version["snapshot_json"])
            wire = await _wire_record(connection, snapshot, version_id=version_id)
            wire["version"] = _version_wire(version)
            # Distinguish the event's exact d from the then-current projection.
            wire["projection_payload"] = wire["payload"]
            wire["payload"], error = decode_json(version["payload_json"])
            wire["derived"]["view"] = "event_version"
            if error:
                wire["payload_error"] = error
            # Recall remains monotonic even when reading a pre-recall version.
            wire["derived"]["recall_state"] = row["recall_state"]
            return wire

    async def query_history(
        self, group_id: str, *, before_record_id: int | None = None,
        record_id: int | None = None, message_id: str = "", sender: str = "",
        keyword: str = "", sent_after: str = "", sent_before: str = "",
        limit: int = 50, versions: bool = False, before_version_id: int | None = None,
        events: bool = False, before_event_record_id: int | None = None,
    ) -> dict[str, Any]:
        """Stable descending keyset paging; every branch binds the current group."""
        if not group_id:
            return {"messages": [], "has_more": False, "next_cursor": None}
        await self.initialize()
        safe_limit = max(1, min(int(limit), 50))
        clauses, parameters = ["group_openid=?"], [group_id]
        for expression, value in (
            ("record_id<?", before_record_id), ("record_id=?", record_id),
            ("message_id=?", message_id),
            ("julianday(sent_at)>=julianday(?)", sent_after),
            ("julianday(sent_at)<=julianday(?)", sent_before),
        ):
            if value is not None and value != "":
                clauses.append(expression)
                parameters.append(value)
        if sender:
            clauses.append("(member_openid=? OR username=?)")
            parameters.extend((sender, sender))
        if keyword:
            clauses.append("instr(content,?)>0")
            parameters.append(keyword)
        async with self._database.read() as connection:
            async with connection.execute(
                f"SELECT * FROM group_messages WHERE {' AND '.join(clauses)} "
                "ORDER BY record_id DESC LIMIT ?", (*parameters, safe_limit + 1),
            ) as cursor:
                rows = await cursor.fetchall()
            page = rows[:safe_limit]
            result = {
                "messages": [await _wire_record(connection, row) for row in page],
                "has_more": len(rows) > safe_limit,
                "next_cursor": page[-1]["record_id"] if len(rows) > safe_limit else None,
            }
            if versions:
                version_clauses = [
                    "v.record_id IN (SELECT record_id FROM group_messages WHERE "
                    + " AND ".join(clauses) + ")"
                ]
                version_parameters = list(parameters)
                if before_version_id is not None:
                    version_clauses.append("v.version_id<?")
                    version_parameters.append(before_version_id)
                async with connection.execute(
                    "SELECT v.* FROM message_versions v "
                    f"WHERE {' AND '.join(version_clauses)} ORDER BY v.version_id DESC LIMIT ?",
                    (*version_parameters, safe_limit + 1),
                ) as cursor:
                    versions_rows = await cursor.fetchall()
                selected = versions_rows[:safe_limit]
                result["versions"] = [_version_wire(row) for row in selected]
                result["versions_has_more"] = len(versions_rows) > safe_limit
                result["next_version_cursor"] = selected[-1]["version_id"] if len(versions_rows) > safe_limit else None
                result["events"] = [
                    await _event_wire(connection, row["event_record_id"])
                    for row in selected if row["event_record_id"] is not None
                ]
            if events:
                event_clauses, event_parameters = ["e.group_openid=?"], [group_id]
                if message_id:
                    event_clauses.append("e.message_id=?")
                    event_parameters.append(message_id)
                if any((before_record_id is not None, record_id is not None,
                        sender, keyword, sent_after, sent_before)):
                    # Unsupported receipts can identify a known message without
                    # having a successful projection/processing.record_id.
                    event_clauses.append(
                        "e.message_id IN (SELECT message_id FROM group_messages WHERE "
                        + " AND ".join(clauses) + ")"
                    )
                    event_parameters.extend(parameters)
                if before_event_record_id is not None:
                    event_clauses.append("e.event_record_id<?")
                    event_parameters.append(before_event_record_id)
                async with connection.execute(
                    "SELECT e.event_record_id FROM group_message_events e "
                    "JOIN message_event_processing p USING(event_record_id) "
                    f"WHERE {' AND '.join(event_clauses)} ORDER BY e.event_record_id DESC LIMIT ?",
                    (*event_parameters, safe_limit + 1),
                ) as cursor:
                    event_rows = await cursor.fetchall()
                result["events"] = [await _event_wire(connection, row[0]) for row in event_rows[:safe_limit]]
                result["events_has_more"] = len(event_rows) > safe_limit
                result["next_event_cursor"] = event_rows[safe_limit - 1][0] if len(event_rows) > safe_limit else None
        return result

    async def get_image_attachment(
        self, group_id: str, record_id: int, attachment_index: int = 0,
        version_id: int | None = None,
    ) -> dict[str, Any] | None:
        if attachment_index < 0:
            return None
        wire = await self.get_full_record(group_id, record_id, version_id)
        if wire is None or wire["derived"]["recall_state"] == "confirmed":
            return None
        images = [item for item in wire["attachments"] if _is_image_attachment(item)]
        return images[attachment_index] if attachment_index < len(images) else None

    async def record_image_read(
        self, group_id: str, record_id: int, *, attachment_id: int | None = None,
        version_id: int | None = None, attachment_index: int = 0,
        status: str, metadata: dict[str, Any] | None = None, error_kind: str = "",
    ) -> bool:
        if attachment_id is None:
            attachment = await self.get_image_attachment(group_id, record_id, attachment_index, version_id)
            if attachment is None:
                return False
            attachment_id = attachment["attachment_id"]
        await self.initialize()
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                "UPDATE message_attachments SET measured_metadata_json=COALESCE(?,measured_metadata_json),"
                "last_read_at=?,last_read_status=?,last_error_kind=? WHERE attachment_id=? AND record_id=? "
                "AND EXISTS (SELECT 1 FROM group_messages m WHERE m.record_id=message_attachments.record_id "
                "AND m.group_openid=?)",
                (encode_json(metadata) if metadata is not None else None, _now(), status,
                 error_kind, attachment_id, record_id, group_id),
            )
            return cursor.rowcount == 1

    async def record_delivery_attempt(
        self, *, group_id: str, source_message_id: str = "", operation: str, status: str,
        parameters: dict[str, Any], result: Any = None, error_type: str = "", message_id: str = "",
    ) -> int:
        await self.initialize()
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                "INSERT INTO message_delivery_attempts(group_openid,source_message_id,message_id,operation,"
                "status,parameters_json,result_json,error_type,received_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (group_id, source_message_id, message_id, operation, status, encode_json(parameters),
                 encode_json(result) if result is not None else None, error_type, _now()),
            )
            return cursor.lastrowid



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
        record=record.record,
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


def _row_to_message(
    row: aiosqlite.Row, full_record: dict[str, Any] | None = None
) -> StoredGroupMessage:
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
        record=full_record if full_record is not None else _basic_wire(dict(row)),
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


def _basic_wire(record: dict[str, Any]) -> dict[str, Any]:
    payload, error = decode_json(record["payload_json"])
    readable = payload if isinstance(payload, dict) else {}
    wire = {
        "record": record,
        "payload": payload,
        "derived": {
            "role": "assistant" if record["is_bot"] else "user",
            "sender_name": record["username"] or ("AiQQ" if record["is_bot"] else "群成员"),
            "reply_summary": _extract_reply_summary(readable),
            "has_image": _has_image(readable),
            "recall_state": "confirmed" if record["recalled_at"] else record.get("recall_state", "unobserved"),
            "progress": record["event_type"] == "BOT_PROGRESS_MESSAGE",
            "view": "current_projection",
        },
    }
    if error:
        wire["payload_error"] = error
    return wire


async def _wire_record(
    connection: aiosqlite.Connection, row: Any, *, version_id: int | None = None
) -> dict[str, Any]:
    record = dict(row)
    wire = _basic_wire(record)
    parameters: tuple[Any, ...] = (record["record_id"],)
    where = "record_id=?"
    if version_id is not None:
        where += " AND version_id=?"
        parameters += (version_id,)
    async with connection.execute(
        f"SELECT * FROM message_attachments WHERE {where} ORDER BY attachment_id", parameters
    ) as cursor:
        attachments = [_attachment_wire(item) for item in await cursor.fetchall()]
    if version_id is None:
        # Resolve every current attachment to an actual immutable source, not a
        # guessed cross-version array merge. A directly nested image object may
        # be enriched across create events; label that projection explicitly.
        by_source = {}
        for item in attachments:
            by_source.setdefault((item["path"], item["metadata_json"]), item)
        projected = []
        for path, metadata in attachment_objects(wire["payload"]):
            exact = by_source.get((path, encode_json(metadata)))
            if exact is not None:
                projected.append(exact)
                continue
            candidates = [item for item in attachments if item["path"] == path]
            url = metadata.get("url")
            url = url if isinstance(url, str) else ""
            anchor = next((item for item in candidates if item["url"] == url and url), None)
            sources = await _attachment_projection_sources(connection, record["record_id"], path)
            projected.append({
                "attachment_id": anchor["attachment_id"] if anchor else None,
                "record_id": record["record_id"], "version_id": None,
                "path": path, "url": url,
                "metadata": metadata, "metadata_json": encode_json(metadata),
                "metadata_source": "current_projection", "source_versions": sources,
                "url_source_attachment_id": anchor["attachment_id"] if anchor else None,
                "measured_metadata": anchor["measured_metadata"] if anchor else None,
                "last_read_at": anchor["last_read_at"] if anchor else None,
                "last_read_status": anchor["last_read_status"] if anchor else None,
                "last_error_kind": anchor["last_error_kind"] if anchor else None,
            })
        attachments = projected
    wire["attachments"] = attachments
    image_index = 0
    for attachment in attachments:
        if _is_image_attachment(attachment):
            attachment["image_attachment_index"] = image_index
            image_index += 1
    wire["latest_event"] = await _event_wire(connection, record.get("last_event_record_id"))
    wire["derived"]["original_gateway_available"] = (
        record.get("first_event_record_id") is not None
        and wire["latest_event"] is not None
        and wire["latest_event"]["source"] == "gateway"
    )
    wire["derived"]["member_lifecycle_support"] = "unverified_gateway_events_only"
    async with connection.execute(
        "SELECT * FROM message_delivery_attempts WHERE group_openid=? "
        "AND (message_id=? OR source_message_id=?) ORDER BY attempt_id",
        (record["group_openid"], record["message_id"], record["message_id"]),
    ) as cursor:
        wire["delivery_attempts"] = []
        for item in await cursor.fetchall():
            attempt = dict(item)
            attempt["parameters"] = json.loads(item["parameters_json"])
            attempt["result"] = json.loads(item["result_json"]) if item["result_json"] is not None else None
            wire["delivery_attempts"].append(attempt)
    return wire


async def _attachment_projection_sources(
    connection: aiosqlite.Connection, record_id: int, path: str
) -> list[dict[str, Any]]:
    """Identify original objects even if URL and MIME arrived separately."""
    async with connection.execute(
        "SELECT version_id,event_record_id,payload_json FROM message_versions "
        "WHERE record_id=? ORDER BY version_id", (record_id,),
    ) as cursor:
        versions = await cursor.fetchall()
    sources = []
    for version in versions:
        node, error = decode_json(version["payload_json"])
        if error:
            continue
        for token in path.split("/")[2:]:
            token = token.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict):
                node = node.get(token)
            elif isinstance(node, list) and token.isdecimal() and int(token) < len(node):
                node = node[int(token)]
            else:
                node = None
                break
        if isinstance(node, dict):
            sources.append({"version_id": version["version_id"],
                            "event_record_id": version["event_record_id"],
                            "path": path, "metadata": node})
    return sources


def _attachment_wire(row: Any) -> dict[str, Any]:
    attachment = dict(row)
    attachment["metadata"] = json.loads(row["metadata_json"])
    attachment["measured_metadata"] = (
        json.loads(row["measured_metadata_json"]) if row["measured_metadata_json"] is not None else None
    )
    return attachment


def _is_image_attachment(attachment: dict[str, Any]) -> bool:
    content_type = attachment["metadata"].get("content_type")
    return isinstance(content_type, str) and content_type.lower().startswith("image/")


def _version_wire(row: Any) -> dict[str, Any]:
    version = dict(row)
    version["payload"], error = decode_json(row["payload_json"])
    version["snapshot"] = json.loads(row["snapshot_json"])
    version["conflicts"] = json.loads(row["conflicts_json"])
    if error:
        version["payload_error"] = error
    version["original_gateway_available"] = (
        row["event_record_id"] is not None and row["source"] == "gateway"
    )
    return version


async def _event_wire(connection: aiosqlite.Connection, event_id: int | None) -> dict[str, Any] | None:
    if event_id is None:
        return None
    row = await _fetch_one(connection,
        "SELECT e.*,p.status,p.record_id,p.error_type,p.processed_at,p.processing_version "
        "FROM group_message_events e JOIN message_event_processing p USING(event_record_id) "
        "WHERE event_record_id=?", (event_id,))
    if row is None:
        return None
    event = dict(row)
    event["envelope"] = json.loads(row["raw_text"])
    return event


async def _fetch_one(connection: aiosqlite.Connection, sql: str, parameters: tuple[Any, ...]):
    async with connection.execute(sql, parameters) as cursor:
        return await cursor.fetchone()
