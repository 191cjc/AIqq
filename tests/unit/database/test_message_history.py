"""End-to-end SQLite contracts for complete context and event provenance."""

import asyncio
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiqq.database.connection import SQLiteConnection
from aiqq.database.group_messages import GroupMessageRepository

from .test_group_messages import group_event


class CompleteHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "messages.db"
        self.database = SQLiteConnection(self.path)
        self.repository = GroupMessageRepository(self.database)
        await self.repository.initialize()

    async def asyncTearDown(self):
        await self.repository.close()
        self.temporary.cleanup()

    async def _capture(self, name="m1", **kwargs):
        event, envelope = group_event(name, **kwargs)
        await self.repository.add_gateway_event(event, envelope)
        return await self.repository.get_full_message(kwargs.get("group_id", "group-1"), name)

    async def test_original_text_unknown_fields_and_future_columns_are_lossless(self):
        async with self.database.transaction() as connection:
            await connection.execute("ALTER TABLE group_messages ADD COLUMN future_json TEXT")
        event, envelope = group_event("complete", content=" 第一行\n\n 第二行  \t")
        envelope.update(op=0, s=24, t=event, unknown_outer={"zero": 0, "null": None})
        envelope["d"]["future"] = [None, False, 0, "", [], {"deep": {"values": [1, 2]}}]
        envelope["d"]["mentions"] = [{"id": "member", "is_you": False}]
        envelope["d"]["attachments"] = [
            {"content_type": "image/gif", "url": f"https://example.com/{index}.gif", "future": index}
            for index in range(25)
        ]
        raw = " \n" + json.dumps(envelope, ensure_ascii=False, indent=2) + "\n"
        await self.repository.add_gateway_event(event, envelope, raw_text=raw, connection_id="test-session")
        wire = await self.repository.get_full_message("group-1", "complete")
        self.assertEqual(wire["payload"], envelope["d"])
        self.assertEqual(wire["record"]["content"], envelope["d"]["content"])
        self.assertIn("future_json", wire["record"])
        self.assertIsNone(wire["record"]["future_json"])
        self.assertEqual(wire["latest_event"]["raw_text"], raw)
        self.assertEqual(wire["latest_event"]["envelope"], envelope)
        self.assertEqual(wire["latest_event"]["connection_id"], "test-session")
        self.assertEqual(len(wire["attachments"]), 25)
        self.assertEqual(wire["attachments"][24]["image_attachment_index"], 24)
        current = await self.repository.get_current_for_reference("group-1", "complete")
        self.assertEqual(current.record, wire)
        self.assertCountEqual(self.path.parent.iterdir(), [self.path, self.path.with_name("messages.db-wal"), self.path.with_name("messages.db-shm")])

    async def test_missing_duplicate_fields_enrich_without_erasing_and_conflicts_have_versions(self):
        event, rich = group_event("m1", image=True)
        rich["d"]["unknown"] = {"keep": False, "empty": None}
        await self.repository.add_gateway_event(event, rich)
        second = {"id": "event-second", "d": {"id": "m1", "group_openid": "group-1", "unknown": {"new": 0}}}
        await self.repository.add_gateway_event(event, second)
        conflict = copy.deepcopy(rich)
        conflict["id"] = "event-third"
        conflict["d"].update(content="other text", attachments=[])
        conflict["d"]["unknown"]["keep"] = 0
        await self.repository.add_gateway_event(event, conflict)
        await self.repository.add_gateway_event(event, conflict)
        full = await self.repository.get_full_message("group-1", "m1")
        self.assertEqual(full["payload"]["unknown"], {"keep": False, "empty": None, "new": 0})
        self.assertEqual(full["payload"]["content"], "hello")
        self.assertEqual(full["payload"]["attachments"], rich["d"]["attachments"])
        self.assertEqual(full["record"]["version_count"], 3)
        self.assertIn("/d/unknown/keep", json.loads(full["record"]["projection_conflicts_json"]))
        history = await self.repository.query_history("group-1", record_id=full["record"]["record_id"], versions=True, events=True)
        self.assertEqual(len(history["versions"]), 3)
        self.assertEqual(history["versions"][0]["payload"], conflict["d"])
        self.assertEqual(len(history["events"]), 4)
        self.assertEqual(history["events"][0]["status"], "duplicate")
        self.assertEqual(len(full["attachments"]), 1)

    async def test_projection_failure_and_restart_preserve_raw_and_only_recover_local_state(self):
        event, envelope = group_event("failed")
        raw = json.dumps(envelope, indent=1)
        with patch.object(self.repository._events, "_project", side_effect=RuntimeError("fixture")):
            with self.assertRaises(RuntimeError):
                await self.repository.add_gateway_event(event, envelope, raw_text=raw)
        async with self.database.read() as connection:
            cursor = await connection.execute("SELECT raw_text,status FROM group_message_events JOIN message_event_processing USING(event_record_id)")
            row = await cursor.fetchone()
            self.assertEqual(tuple(row), (raw, "failed"))
        await self.repository.close()
        self.repository = GroupMessageRepository(SQLiteConnection(self.path))
        await self.repository.initialize()
        wire = await self.repository.get_full_message("group-1", "failed")
        self.assertEqual(wire["latest_event"]["status"], "applied")
        self.assertEqual(wire["record"]["version_count"], 1)
        self.assertEqual((await self.repository.storage_status())["unprojected_events"], 0)
        self.assertEqual(await self.repository.recover_pending_events(), {"recovered": 0, "failed": 0})

    async def test_projection_cancellation_rolls_back_but_keeps_pending_receipt(self):
        event, envelope = group_event("cancelled")
        with patch.object(self.repository._events, "_project", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await self.repository.add_gateway_event(event, envelope)
        status = await self.repository.storage_status()
        self.assertEqual(status["group_message_events"], 1)
        self.assertEqual(status["unprojected_events"], 1)
        await self.repository.recover_pending_events()
        self.assertIsNotNone(await self.repository.get_full_message("group-1", "cancelled"))

    async def test_same_message_id_across_groups_and_foreign_record_version_rejected(self):
        first = await self._capture("same", group_id="group-a", content="A")
        second = await self._capture("same", group_id="group-b", content="B")
        self.assertNotEqual(first["record"]["record_id"], second["record"]["record_id"])
        self.assertIsNone(await self.repository.get_full_record("group-b", first["record"]["record_id"]))
        versions = await self.repository.query_history("group-a", versions=True)
        version_id = versions["versions"][0]["version_id"]
        self.assertIsNone(await self.repository.get_full_record("group-b", second["record"]["record_id"], version_id))
        self.assertEqual((await self.repository.query_history("group-b"))["messages"][0]["payload"]["content"], "B")

    async def test_unknown_group_events_are_archived_without_inventing_recall(self):
        await self._capture("m1")
        envelope = {"op": 0, "t": "GROUP_FUTURE_EVENT", "s": 2, "id": "future", "d": {
            "group_openid": "group-1", "id": "m1", "future": True,
        }}
        self.assertFalse(await self.repository.add_gateway_event(envelope["t"], envelope))
        history = await self.repository.query_history("group-1", events=True)
        self.assertEqual(history["events"][0]["status"], "unsupported")
        self.assertEqual(history["events"][0]["envelope"], envelope)
        self.assertEqual(history["messages"][0]["derived"]["recall_state"], "unobserved")
        self.assertEqual((await self.repository.query_history("group-other", events=True))["events"], [])

    async def test_version_and_event_queries_share_message_filters(self):
        first = await self._capture("1", content="selected word")
        await self._capture("2", content="unrelated")
        unsupported = {"t": "GROUP_FUTURE_EVENT", "d": {"id": "1", "group_openid": "group-1"}}
        await self.repository.add_gateway_event(unsupported["t"], unsupported)
        for filters in (
            {"record_id": first["record"]["record_id"]},
            {"sender": "member-1", "keyword": "selected"},
            {"before_record_id": first["record"]["record_id"] + 1},
        ):
            result = await self.repository.query_history("group-1", versions=True, events=True, **filters)
            self.assertEqual([item["record"]["message_id"] for item in result["messages"]], ["1"])
            self.assertEqual([item["record_id"] for item in result["versions"]], [first["record"]["record_id"]])
            self.assertEqual([item["message_id"] for item in result["events"]], ["1", "1"])
            self.assertEqual(result["events"][0]["status"], "unsupported")
        unprojected = {"t": "GROUP_FUTURE_EVENT", "d": {"id": "not-created", "group_openid": "group-1"}}
        await self.repository.add_gateway_event(unprojected["t"], unprojected)
        result = await self.repository.query_history("group-1", message_id="not-created", events=True)
        self.assertEqual(result["messages"], [])
        self.assertEqual(len(result["events"]), 1)

    async def test_fifty_full_text_records_and_stable_filtered_paging(self):
        contents = []
        for index in range(55):
            text = f"line {index}\n" + "  原文\n" * 100
            contents.append(text)
            await self._capture(str(index), content=text)
        current = await self._capture("current")
        recent = await self.repository.list_for_reference("group-1", before_message_id="current", limit=50)
        self.assertEqual([row.content for row in recent], contents[-50:])
        self.assertGreater(sum(len(row.content) for row in recent), 10000)
        first_page = await self.repository.query_history("group-1", before_record_id=current["record"]["record_id"], limit=30)
        second_page = await self.repository.query_history("group-1", before_record_id=first_page["next_cursor"], limit=30)
        ids = [row["record"]["record_id"] for row in first_page["messages"] + second_page["messages"]]
        self.assertEqual(len(ids), 55)
        self.assertEqual(len(set(ids)), 55)
        self.assertFalse(second_page["has_more"])
        filtered = await self.repository.query_history("group-1", sender="member-12", keyword="line 12\n")
        self.assertEqual(len(filtered["messages"]), 1)
        self.assertEqual(filtered["messages"][0]["record"]["message_id"], "12")

    async def test_corrupt_and_nonobject_payloads_preserve_raw_and_error(self):
        for name, payload, error, decoded in (("broken", "{bad", "invalid_json", None), ("list", "[false,null,0]", "non_object", [False, None, 0])):
            wire = await self._capture(name)
            async with self.database.transaction() as connection:
                await connection.execute("UPDATE group_messages SET payload_json=? WHERE record_id=?", (payload, wire["record"]["record_id"]))
            wire = await self.repository.get_full_message("group-1", name)
            self.assertEqual(wire["record"]["payload_json"], payload)
            self.assertEqual(wire["payload_error"], error)
            self.assertEqual(wire["payload"], decoded)

    async def test_new_receipt_attaches_first_original_event_to_legacy_projection(self):
        wire = await self._capture("legacy")
        record_id = wire["record"]["record_id"]
        async with self.database.transaction() as connection:
            await connection.execute(
                "UPDATE group_messages SET first_event_record_id=NULL,last_event_record_id=NULL "
                "WHERE record_id=?", (record_id,),
            )
        event, envelope = group_event("legacy")
        envelope["id"] = "new-receipt"
        await self.repository.add_gateway_event(event, envelope)
        latest = await self.repository.get_full_record("group-1", record_id)
        self.assertEqual(latest["record"]["record_id"], record_id)
        self.assertEqual(latest["record"]["first_event_record_id"], latest["latest_event"]["event_record_id"])
        self.assertTrue(latest["derived"]["original_gateway_available"])

    async def test_time_filter_compares_instants_across_offsets(self):
        for name, timestamp in (("earlier", "2026-09-16T08:00:00+08:00"), ("later", "2026-09-16T02:00:00Z")):
            event, envelope = group_event(name)
            envelope["d"]["timestamp"] = timestamp
            await self.repository.add_gateway_event(event, envelope)
        result = await self.repository.query_history(
            "group-1", sent_after="2026-09-16T01:00:00Z", sent_before="2026-09-16T03:00:00Z",
        )
        self.assertEqual([item["record"]["message_id"] for item in result["messages"]], ["later"])

    async def test_attachment_order_provenance_measurements_and_same_group_updates(self):
        event, envelope = group_event("m1", image=True)
        envelope["d"]["attachments"].insert(0, {"content_type": "text/plain", "url": "https://example.com/text"})
        envelope["d"]["msg_elements"] = [{"nested": {"attachments": [{"content_type": "image/gif", "url": "https://example.com/gif", "flag": None}]}}]
        await self.repository.add_gateway_event(event, envelope)
        wire = await self.repository.get_full_message("group-1", "m1")
        record_id = wire["record"]["record_id"]
        first = await self.repository.get_image_attachment("group-1", record_id, 0)
        second = await self.repository.get_image_attachment("group-1", record_id, 1)
        self.assertEqual(first["path"], "/d/attachments/1")
        self.assertEqual(second["path"], "/d/msg_elements/0/nested/attachments/0")
        self.assertEqual(second["image_attachment_index"], 1)
        self.assertFalse(await self.repository.record_image_read("wrong-group", record_id, attachment_id=first["attachment_id"], status="ok"))
        self.assertTrue(await self.repository.record_image_read("group-1", record_id, attachment_id=first["attachment_id"], status="ok", metadata={"width": 20, "sha256": "fixture"}))
        await self.repository.record_image_read("group-1", record_id, status="error", error_kind="expired")
        read = await self.repository.get_image_attachment("group-1", record_id)
        self.assertEqual(read["measured_metadata"], {"width": 20, "sha256": "fixture"})
        self.assertEqual(read["last_error_kind"], "expired")
        self.assertEqual(read["metadata"], envelope["d"]["attachments"][1])

    async def test_enriched_direct_image_objects_keep_readable_projection_and_source_versions(self):
        for name, first_image, second_image in (
            ("enriched", {"content_type": "image/jpeg", "url": "https://example.com/a.jpg"},
             {"content_type": "image/jpeg", "url": "https://example.com/a.jpg", "width": 20}),
            ("split", {"content_type": "image/jpeg"}, {"url": "https://example.com/b.jpg"}),
        ):
            event, envelope = group_event(name)
            envelope["d"]["nested"] = {"image": first_image}
            await self.repository.add_gateway_event(event, envelope)
            later = {"id": "next-" + name, "d": {
                "id": name, "group_openid": "group-1", "nested": {"image": second_image},
            }}
            await self.repository.add_gateway_event(event, later)
            wire = await self.repository.get_full_message("group-1", name)
            self.assertEqual(len(wire["attachments"]), 1)
            attachment = await self.repository.get_image_attachment("group-1", wire["record"]["record_id"])
            self.assertEqual(attachment["url"], second_image["url"])
            if name == "split":
                self.assertEqual(attachment["metadata_source"], "current_projection")
                self.assertIsNone(attachment["version_id"])
                self.assertEqual([source["metadata"] for source in attachment["source_versions"]],
                                 [first_image, second_image])

        # Neither source contains the merged dimensions and filename together.
        event, envelope = group_event("composite")
        envelope["d"]["image"] = {"content_type": "image/jpeg", "url": "https://example.com/c.jpg", "width": 20}
        await self.repository.add_gateway_event(event, envelope)
        later = copy.deepcopy(envelope)
        later["id"] = "composite-later"
        later["d"]["image"].pop("width")
        later["d"]["image"]["filename"] = "c.jpg"
        await self.repository.add_gateway_event(event, later)
        wire = await self.repository.get_full_message("group-1", "composite")
        attachment = await self.repository.get_image_attachment("group-1", wire["record"]["record_id"])
        self.assertEqual(attachment["metadata_source"], "current_projection")
        self.assertIsNone(attachment["version_id"])
        self.assertEqual(attachment["metadata"]["width"], 20)
        self.assertEqual(attachment["metadata"]["filename"], "c.jpg")
        self.assertEqual(len(attachment["source_versions"]), 2)
        await self.repository.record_image_read("group-1", wire["record"]["record_id"],
                                               attachment_id=attachment["attachment_id"], status="ok",
                                               metadata={"width": 20})
        measured = await self.repository.get_image_attachment("group-1", wire["record"]["record_id"])
        self.assertEqual(measured["measured_metadata"], {"width": 20})

    async def test_local_recall_before_late_create_is_monotonic_and_blocks_old_image_version(self):
        self.assertFalse(await self.repository.mark_recalled(group_id="group-1", message_id="bot", recalled_at="2026-09-16T01:00:00Z"))
        parameters = dict(message_id="bot", group_openid="group-1", username="AiQQ", content="full answer", message_type=7, sent_at="2026-09-16T00:00:00Z", payload={"attachments": [{"content_type": "image/jpeg", "url": "https://example.com/bot.jpg"}]})
        await self.repository.add_bot_message(**parameters)
        await self.repository.add_bot_message(**parameters)
        wire = await self.repository.get_full_message("group-1", "bot")
        self.assertEqual(wire["derived"]["recall_state"], "confirmed")
        self.assertEqual(wire["record"]["recalled_at"], "2026-09-16T01:00:00Z")
        self.assertIsNone(await self.repository.get_image_attachment("group-1", wire["record"]["record_id"]))
        self.assertEqual((await self.repository.storage_status())["unprojected_events"], 0)
        versions = await self.repository.query_history("group-1", message_id="bot", versions=True)
        self.assertEqual(len(versions["versions"]), 2)
        first_version = versions["versions"][-1]["version_id"]
        self.assertIsNone(await self.repository.get_image_attachment("group-1", wire["record"]["record_id"], version_id=first_version))

    async def test_failed_delivery_is_an_attempt_without_fake_message(self):
        await self._capture("source")
        await self.repository.record_delivery_attempt(group_id="group-1", source_message_id="source", operation="image", status="failed", parameters={"content": None, "msg_type": 7}, error_type="ServerError")
        rows = await self.repository.query_history("group-1")
        self.assertEqual(len(rows["messages"]), 1)
        self.assertEqual(rows["messages"][0]["delivery_attempts"][0]["status"], "failed")
        self.assertEqual((await self.repository.query_history("other"))["messages"], [])


class LegacyMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_preserves_all_columns_ids_payloads_indexes_and_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            with sqlite3.connect(path) as connection:
                connection.execute("""CREATE TABLE group_messages (
                    record_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL UNIQUE,event_id TEXT NOT NULL DEFAULT '',
                    event_type TEXT NOT NULL,group_openid TEXT NOT NULL,
                    member_openid TEXT NOT NULL DEFAULT '',username TEXT NOT NULL DEFAULT '',
                    member_role TEXT NOT NULL DEFAULT '',is_bot INTEGER NOT NULL DEFAULT 0,
                    content TEXT NOT NULL DEFAULT '',message_type INTEGER,sent_at TEXT NOT NULL DEFAULT '',
                    received_at TEXT NOT NULL,payload_json TEXT NOT NULL,recalled_at TEXT NOT NULL DEFAULT '',
                    future_column TEXT DEFAULT NULL)""")
                connection.execute("CREATE INDEX custom_future_index ON group_messages(future_column)")
                connection.execute("INSERT INTO group_messages(record_id,message_id,event_type,group_openid,content,received_at,payload_json,recalled_at,future_column) VALUES (42,'old','BOT_MESSAGE_CREATE','group-1','full\n answer','2026-09-16','{ \"z\": false, \"x\": null }','2026-09-16','future')")
                connection.execute("UPDATE sqlite_sequence SET seq=80 WHERE name='group_messages'")
                original = connection.execute("SELECT * FROM group_messages").fetchone()
            repository = GroupMessageRepository(SQLiteConnection(path))
            try:
                await repository.initialize()
                wire = await repository.get_full_message("group-1", "old")
                self.assertEqual(wire["record"]["record_id"], 42)
                self.assertEqual(wire["record"]["future_column"], "future")
                self.assertEqual(wire["record"]["payload_json"], '{ "z": false, "x": null }')
                self.assertEqual(wire["record"]["content"], "full\n answer")
                self.assertEqual(wire["derived"]["recall_state"], "confirmed")
                versions = await repository.query_history("group-1", versions=True)
                self.assertEqual(versions["versions"][0]["source"], "legacy_snapshot")
                self.assertFalse(versions["versions"][0]["original_gateway_available"])
                event, envelope = group_event("old", group_id="group-2")
                await repository.add_gateway_event(event, envelope)
                other = await repository.get_full_message("group-2", "old")
                self.assertGreater(other["record"]["record_id"], 80)
                with sqlite3.connect(path) as connection:
                    actual = connection.execute("SELECT * FROM group_messages WHERE record_id=42").fetchone()
                    self.assertEqual(actual[:len(original)], original)
                    self.assertIsNotNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name='custom_future_index'").fetchone())
                await repository.close()
                await repository.initialize()
                self.assertEqual(len((await repository.query_history("group-1", versions=True))["versions"]), 1)
            finally:
                await repository.close()


if __name__ == "__main__":
    unittest.main()
