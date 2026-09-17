"""Explicit, id-preserving migrations for the existing AiQQ message database."""

from __future__ import annotations

import json
import re

import aiosqlite

from .history import attachment_objects, encode_json


async def migrate_group_messages(connection: aiosqlite.Connection) -> None:
    await connection.execute(
        """
        CREATE TABLE IF NOT EXISTS group_messages (
            record_id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT NOT NULL,
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
            recalled_at TEXT NOT NULL DEFAULT '',
            UNIQUE(group_openid, message_id)
        )
        """
    )
    async with connection.execute("PRAGMA table_info(group_messages)") as cursor:
        columns = {row[1] for row in await cursor.fetchall()}
    if "recalled_at" not in columns:
        await connection.execute(
            "ALTER TABLE group_messages ADD COLUMN recalled_at TEXT NOT NULL DEFAULT ''"
        )
    await _group_scoped_identity(connection)
    additions = {
        "first_event_record_id": "INTEGER",
        "last_event_record_id": "INTEGER",
        "version_count": "INTEGER NOT NULL DEFAULT 0",
        "updated_at": "TEXT NOT NULL DEFAULT ''",
        "update_source": "TEXT NOT NULL DEFAULT 'legacy_snapshot'",
        "recall_state": "TEXT NOT NULL DEFAULT 'unobserved'",
        "projection_conflicts_json": "TEXT NOT NULL DEFAULT '[]'",
    }
    for name, declaration in additions.items():
        if name not in columns:
            await connection.execute(f"ALTER TABLE group_messages ADD COLUMN {name} {declaration}")
    await connection.execute(
        "UPDATE group_messages SET recall_state = 'confirmed' WHERE recalled_at <> ''"
    )
    for name, keys in (
        ("group_record", "group_openid, record_id DESC"),
        ("member_record", "group_openid, member_openid, record_id DESC"),
        ("reference", "group_openid, event_type, recalled_at, record_id DESC"),
    ):
        await connection.execute(
            f"CREATE INDEX IF NOT EXISTS idx_group_messages_{name} ON group_messages ({keys})"
        )
    statements = [
        """CREATE TABLE IF NOT EXISTS group_message_events (
            event_record_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL, group_openid TEXT NOT NULL DEFAULT '',
            message_id TEXT NOT NULL DEFAULT '', event_id TEXT NOT NULL DEFAULT '',
            connection_id TEXT NOT NULL DEFAULT '', sequence_json TEXT,
            received_at TEXT NOT NULL, source TEXT NOT NULL,
            raw_text TEXT NOT NULL, raw_sha256 TEXT NOT NULL,
            dedup_key TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS message_event_processing (
            event_record_id INTEGER PRIMARY KEY REFERENCES group_message_events(event_record_id),
            status TEXT NOT NULL DEFAULT 'pending', record_id INTEGER,
            error_type TEXT NOT NULL DEFAULT '', processed_at TEXT NOT NULL DEFAULT '',
            processing_version INTEGER NOT NULL DEFAULT 1
        )""",
        """CREATE INDEX IF NOT EXISTS idx_message_events_group
            ON group_message_events(group_openid, event_record_id DESC)""",
        """CREATE INDEX IF NOT EXISTS idx_message_events_dedup
            ON group_message_events(dedup_key)""",
        """CREATE TABLE IF NOT EXISTS message_versions (
            version_id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER NOT NULL REFERENCES group_messages(record_id),
            event_record_id INTEGER UNIQUE REFERENCES group_message_events(event_record_id),
            source TEXT NOT NULL, received_at TEXT NOT NULL,
            payload_json TEXT NOT NULL, snapshot_json TEXT NOT NULL,
            conflicts_json TEXT NOT NULL DEFAULT '[]'
        )""",
        """CREATE INDEX IF NOT EXISTS idx_message_versions_record
            ON message_versions(record_id, version_id DESC)""",
        """CREATE TABLE IF NOT EXISTS message_attachments (
            attachment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER NOT NULL REFERENCES group_messages(record_id),
            version_id INTEGER NOT NULL REFERENCES message_versions(version_id),
            path TEXT NOT NULL, url TEXT NOT NULL DEFAULT '', metadata_json TEXT NOT NULL,
            measured_metadata_json TEXT, last_read_at TEXT,
            last_read_status TEXT, last_error_kind TEXT,
            UNIQUE(version_id, path)
        )""",
        """CREATE INDEX IF NOT EXISTS idx_message_attachments_record
            ON message_attachments(record_id, version_id, attachment_id)""",
        """CREATE TABLE IF NOT EXISTS message_delivery_attempts (
            attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_openid TEXT NOT NULL, source_message_id TEXT NOT NULL DEFAULT '',
            message_id TEXT NOT NULL DEFAULT '', operation TEXT NOT NULL,
            status TEXT NOT NULL, parameters_json TEXT NOT NULL, result_json TEXT,
            error_type TEXT NOT NULL DEFAULT '', received_at TEXT NOT NULL
        )""",
        """CREATE INDEX IF NOT EXISTS idx_message_delivery_group
            ON message_delivery_attempts(group_openid, message_id, source_message_id)
        """,
    ]
    for sql in statements:
        await connection.execute(sql)
    # Snapshot only records without any version. Subsequent starts are idempotent.
    async with connection.execute(
        "SELECT * FROM group_messages WHERE NOT EXISTS "
        "(SELECT 1 FROM message_versions WHERE message_versions.record_id=group_messages.record_id)"
    ) as cursor:
        rows = await cursor.fetchall()
    for row in rows:
        snapshot = dict(row)
        version_cursor = await connection.execute(
            "INSERT INTO message_versions(record_id,source,received_at,payload_json,snapshot_json) "
            "VALUES (?,'legacy_snapshot',?,?,?)",
            (row["record_id"], row["received_at"], row["payload_json"], encode_json(snapshot)),
        )
        try:
            payload = json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError):
            payload = None
        await insert_attachments(connection, row["record_id"], version_cursor.lastrowid, payload)
        await connection.execute(
            "UPDATE group_messages SET version_count=1 WHERE record_id=?", (row["record_id"],)
        )


async def _group_scoped_identity(connection: aiosqlite.Connection) -> None:
    """Remove only the known legacy global key, retaining all columns and ids.

    Rebuild from SQLite's actual DDL, not a field whitelist, so future columns,
    custom indexes and triggers survive. Abort if an unfamiliar unique constraint
    cannot be safely rewritten instead of silently merging cross-group messages.
    """
    async with connection.execute("PRAGMA index_list(group_messages)") as cursor:
        indexes = await cursor.fetchall()
    global_unique = []
    for index in indexes:
        if not index[2]:
            continue
        index_name = str(index[1]).replace('"', '""')
        async with connection.execute(f'PRAGMA index_info("{index_name}")') as cursor:
            fields = [item[2] for item in await cursor.fetchall()]
        if fields == ["message_id"]:
            global_unique.append(index)
    if not global_unique:
        return
    async with connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='group_messages'"
    ) as cursor:
        ddl = (await cursor.fetchone())[0]
    rewritten, count = re.subn(
        r"(\bmessage_id\s+TEXT\s+NOT\s+NULL)\s+UNIQUE\b", r"\1", ddl, flags=re.IGNORECASE
    )
    if any(index[3] == "u" for index in global_unique) and count != 1:
        raise RuntimeError("unsupported legacy message identity constraint")
    rewritten = re.sub(
        r"(CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)[\"`\[]?group_messages[\"`\]]?",
        r"\1group_messages_r11_migration", rewritten, count=1, flags=re.IGNORECASE,
    )
    async with connection.execute(
        "SELECT name,sql FROM sqlite_master WHERE tbl_name='group_messages' "
        "AND type IN ('index','trigger') AND sql IS NOT NULL"
    ) as cursor:
        objects = await cursor.fetchall()
    async with connection.execute("PRAGMA table_info(group_messages)") as cursor:
        names = [row[1] for row in await cursor.fetchall()]
    async with connection.execute(
        "SELECT seq FROM sqlite_sequence WHERE name='group_messages'"
    ) as cursor:
        sequence_row = await cursor.fetchone()
    quoted = ",".join('"' + name.replace('"', '""') + '"' for name in names)
    await connection.execute(rewritten)
    await connection.execute(
        f"INSERT INTO group_messages_r11_migration({quoted}) SELECT {quoted} FROM group_messages"
    )
    await connection.execute("DROP TABLE group_messages")
    await connection.execute("ALTER TABLE group_messages_r11_migration RENAME TO group_messages")
    if sequence_row is not None:
        await connection.execute(
            "UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name='group_messages'",
            (sequence_row[0],),
        )
    global_names = {index[1] for index in global_unique}
    for name, sql in objects:
        if name not in global_names:
            await connection.execute(sql)
    await connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_group_messages_identity "
        "ON group_messages(group_openid,message_id)"
    )


async def insert_attachments(
    connection: aiosqlite.Connection, record_id: int, version_id: int, payload: object
) -> None:
    for path, metadata in attachment_objects(payload):
        url = metadata.get("url")
        await connection.execute(
            "INSERT INTO message_attachments(record_id,version_id,path,url,metadata_json) VALUES (?,?,?,?,?)",
            (record_id, version_id, path, url if isinstance(url, str) else "", encode_json(metadata)),
        )
