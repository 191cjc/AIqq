"""Durable group-event ingestion and replayable local message projections.

This module performs SQLite operations only. Recovery has no business callback,
model client, network downloader or QQ sender and cannot replay those effects.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from .connection import SQLiteConnection
from .history import decode_json, encode_json, merge_missing
from .migrations import insert_attachments

CREATE_EVENTS = {"GROUP_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE"}
LOCAL_CREATE_EVENTS = {"BOT_MESSAGE_CREATE", "BOT_PROGRESS_MESSAGE"}
LOCAL_RECALL_EVENT = "LOCAL_BOT_RECALL_CONFIRMED"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def string(value: Any) -> str:
    return value if isinstance(value, str) else ""


class MessageEventStore:
    def __init__(self, database: SQLiteConnection) -> None:
        self.database = database
        self.write_failures = 0

    async def add(
        self, event_type: str, envelope: dict[str, Any], *, raw_text: str | None = None,
        connection_id: str = "", source: str = "gateway",
    ) -> bool:
        data = envelope.get("d")
        data = data if isinstance(data, dict) else {}
        local = envelope.get("record", {}) if source == "local_delivery" else {}
        group_id = string(local.get("group_openid", data.get("group_openid")))
        message_id = string(local.get("message_id", data.get("id")))
        raw = raw_text if raw_text is not None else encode_json(envelope)
        # A supplied original must describe the same logical envelope; never
        # associate untrusted or mismatched bytes with a different projection.
        if raw_text is not None and json.loads(raw_text) != envelope:
            raise ValueError("gateway raw text does not match parsed event")
        dedup = dict(envelope)
        dedup.pop("s", None)
        dedup_key = hashlib.sha256(
            encode_json([source, event_type, dedup]).encode("utf-8")
        ).hexdigest()
        try:
            async with self.database.transaction() as connection:
                cursor = await connection.execute(
                    "INSERT INTO group_message_events(event_type,group_openid,message_id,event_id,"
                    "connection_id,sequence_json,received_at,source,raw_text,raw_sha256,dedup_key) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (event_type, group_id, message_id, string(envelope.get("id")), connection_id,
                     encode_json(envelope["s"]) if "s" in envelope else None, now(), source,
                     raw, hashlib.sha256(raw.encode("utf-8")).hexdigest(), dedup_key),
                )
                event_record_id = cursor.lastrowid
                await connection.execute(
                    "INSERT INTO message_event_processing(event_record_id) VALUES (?)", (event_record_id,)
                )
        except BaseException:
            self.write_failures += 1
            raise
        # Deliberately separate transactions: projection rollback cannot erase
        # the original receipt and its pending state.
        return await self.project(event_record_id)

    async def project(self, event_record_id: int) -> bool:
        try:
            async with self.database.transaction() as connection:
                return await self._project(connection, event_record_id)
        except Exception as exc:
            self.write_failures += 1
            async with self.database.transaction() as connection:
                await connection.execute(
                    "UPDATE message_event_processing SET status='failed',error_type=?,processed_at=? "
                    "WHERE event_record_id=?", (type(exc).__name__, now(), event_record_id),
                )
            raise

    async def recover(self) -> dict[str, int]:
        async with self.database.read() as connection:
            async with connection.execute(
                "SELECT event_record_id FROM message_event_processing "
                "WHERE status IN ('pending','failed') ORDER BY event_record_id"
            ) as cursor:
                rows = await cursor.fetchall()
        recovered = failed = 0
        for row in rows:
            try:
                recovered += bool(await self.project(row[0]))
            except Exception:
                failed += 1
        return {"recovered": recovered, "failed": failed}

    async def _project(self, connection: aiosqlite.Connection, event_record_id: int) -> bool:
        event = await _one(connection,
            "SELECT e.*,p.status FROM group_message_events e JOIN message_event_processing p "
            "USING(event_record_id) WHERE event_record_id=?", (event_record_id,))
        if event is None or event["status"] not in {"pending", "failed"}:
            return False
        event_type = event["event_type"]
        if not event["group_openid"] or not event["message_id"] or event_type not in (
            CREATE_EVENTS | LOCAL_CREATE_EVENTS | {LOCAL_RECALL_EVENT}
        ):
            await _status(connection, event_record_id, "unsupported")
            return False
        duplicate = await _one(connection,
            "SELECT p.record_id FROM group_message_events e JOIN message_event_processing p "
            "USING(event_record_id) WHERE e.dedup_key=? AND e.event_record_id<>? "
            "AND p.status='applied' ORDER BY e.event_record_id LIMIT 1",
            (event["dedup_key"], event_record_id))
        if duplicate is not None:
            await _status(connection, event_record_id, "duplicate", duplicate[0])
            return event_type in CREATE_EVENTS | LOCAL_CREATE_EVENTS
        envelope = json.loads(event["raw_text"])
        raw_payload = envelope["d"]
        previous = await _one(connection,
            "SELECT * FROM group_messages WHERE group_openid=? AND message_id=?",
            (event["group_openid"], event["message_id"]))
        if event_type == LOCAL_RECALL_EVENT:
            if previous is None:
                # Only trusted local confirmation creates this event. Keep it
                # pending for a late successful-send projection.
                return False
            if not previous["is_bot"]:
                await _status(connection, event_record_id, "unsupported")
                return False
            await connection.execute(
                "UPDATE group_messages SET recalled_at=CASE WHEN recalled_at='' THEN ? ELSE recalled_at END,"
                "recall_state='confirmed',last_event_record_id=?,updated_at=?,update_source=? "
                "WHERE record_id=?",
                (string(raw_payload.get("recalled_at")) or event["received_at"], event_record_id,
                 event["received_at"], "local_recall", previous["record_id"]),
            )
            await self._version(connection, event, raw_payload, previous["record_id"], [])
            return True
        conflicts: list[str] = []
        payload = raw_payload
        if previous is not None:
            old_payload, error = decode_json(previous["payload_json"])
            if not error:
                payload, conflicts = merge_missing(old_payload, raw_payload)
            else:
                # The unreadable historical payload remains in its legacy
                # snapshot; do not pretend it was an empty valid object.
                conflicts = ["/d:previous_payload_invalid"]
        author = payload.get("author")
        author = author if isinstance(author, dict) else {}
        is_local = event_type in LOCAL_CREATE_EVENTS
        local_record = envelope.get("record", {}) if is_local else {}
        if not isinstance(local_record, dict):
            local_record = {}
        message_type = local_record.get("message_type", payload.get("message_type"))
        if isinstance(message_type, bool) or not isinstance(message_type, int):
            message_type = None
        values = {
            "message_id": event["message_id"], "event_id": event["event_id"],
            "event_type": event_type, "group_openid": event["group_openid"],
            "member_openid": string(author.get("member_openid") or author.get("id")),
            "username": string(local_record.get("username")) if is_local else string(author.get("username")),
            "member_role": "bot" if is_local else string(author.get("member_role")),
            "is_bot": int(is_local or author.get("bot") is True),
            "content": string(local_record.get("content")) if is_local else string(payload.get("content")),
            "message_type": message_type,
            "sent_at": string(local_record.get("sent_at")) if is_local else string(payload.get("timestamp")),
            "received_at": event["received_at"], "payload_json": encode_json(payload),
            "first_event_record_id": event_record_id, "last_event_record_id": event_record_id,
            "updated_at": event["received_at"], "update_source": event["source"],
            "projection_conflicts_json": encode_json(conflicts),
        }
        if previous is None:
            columns = ",".join(values)
            placeholders = ",".join("?" for _ in values)
            cursor = await connection.execute(
                f"INSERT INTO group_messages({columns}) VALUES ({placeholders})", tuple(values.values()))
            record_id = cursor.lastrowid
        else:
            record_id = previous["record_id"]
            # Existing non-empty convenience columns are established facts.
            # Preserve initial order/time and recall state. Only enrich missing
            # columns from enriched payload or add explicit AT event evidence.
            for name in ("message_id", "group_openid", "received_at"):
                values.pop(name)
            if previous["first_event_record_id"] is not None:
                values.pop("first_event_record_id")
            for name in ("member_openid", "username", "member_role", "content", "sent_at"):
                if previous[name] not in ("", None):
                    values[name] = previous[name]
            values["is_bot"] = int(previous["is_bot"] or values["is_bot"])
            if previous["message_type"] is not None:
                values["message_type"] = previous["message_type"]
            if previous["event_type"] == "GROUP_AT_MESSAGE_CREATE":
                values["event_type"] = previous["event_type"]
            prior_conflicts, _ = decode_json(previous["projection_conflicts_json"])
            if isinstance(prior_conflicts, list):
                values["projection_conflicts_json"] = encode_json(sorted(set(prior_conflicts + conflicts)))
            assignments = ",".join(f"{name}=?" for name in values)
            await connection.execute(
                f"UPDATE group_messages SET {assignments} WHERE record_id=?", (*values.values(), record_id))
        if is_local:
            recall = await _one(connection,
                "SELECT event_record_id,raw_text,received_at FROM group_message_events "
                "WHERE group_openid=? AND message_id=? AND event_type=? ORDER BY event_record_id LIMIT 1",
                (event["group_openid"], event["message_id"], LOCAL_RECALL_EVENT))
            if recall is not None:
                recall_payload = json.loads(recall["raw_text"])["d"]
                await connection.execute(
                    "UPDATE group_messages SET recalled_at=CASE WHEN recalled_at='' THEN ? ELSE recalled_at END,"
                    "recall_state='confirmed' WHERE record_id=?",
                    (string(recall_payload.get("recalled_at")) or recall["received_at"], record_id))
        await self._version(connection, event, raw_payload, record_id, conflicts)
        if is_local:
            async with connection.execute(
                "SELECT e.event_record_id FROM group_message_events e "
                "JOIN message_event_processing p USING(event_record_id) "
                "WHERE e.group_openid=? AND e.message_id=? AND e.event_type=? "
                "AND p.status IN ('pending','failed') ORDER BY e.event_record_id",
                (event["group_openid"], event["message_id"], LOCAL_RECALL_EVENT),
            ) as cursor:
                pending_recalls = await cursor.fetchall()
            for pending_recall in pending_recalls:
                await self._project(connection, pending_recall[0])
        return True

    async def _version(
        self, connection: aiosqlite.Connection, event: aiosqlite.Row,
        raw_payload: dict[str, Any], record_id: int, conflicts: list[str],
    ) -> None:
        await connection.execute(
            "UPDATE group_messages SET version_count=version_count+1 WHERE record_id=?", (record_id,))
        snapshot = await _one(connection, "SELECT * FROM group_messages WHERE record_id=?", (record_id,))
        cursor = await connection.execute(
            "INSERT INTO message_versions(record_id,event_record_id,source,received_at,payload_json,"
            "snapshot_json,conflicts_json) VALUES (?,?,?,?,?,?,?)",
            (record_id, event["event_record_id"], event["source"], event["received_at"],
             encode_json(raw_payload), encode_json(dict(snapshot)), encode_json(conflicts)),
        )
        # A recall event carries no media. Its snapshot remains readable, while
        # source attachments retain the original creation version provenance.
        await insert_attachments(connection, record_id, cursor.lastrowid, raw_payload)
        await _status(connection, event["event_record_id"], "applied", record_id)


async def _one(connection: aiosqlite.Connection, sql: str, parameters: tuple[Any, ...]):
    async with connection.execute(sql, parameters) as cursor:
        return await cursor.fetchone()


async def _status(
    connection: aiosqlite.Connection, event_record_id: int, status: str, record_id: int | None = None
) -> None:
    await connection.execute(
        "UPDATE message_event_processing SET status=?,record_id=?,processed_at=?,error_type='' "
        "WHERE event_record_id=?", (status, record_id, now(), event_record_id))
