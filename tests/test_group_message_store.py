import os
import tempfile
import unittest
from unittest.mock import patch

from group_message_store import GroupMessageStore


def group_event(
    message_id="message-1",
    *,
    group_openid="group-1",
    content="大家早上好",
):
    return {
        "id": f"event-{message_id}",
        "op": 0,
        "t": "GROUP_MESSAGE_CREATE",
        "d": {
            "id": message_id,
            "group_openid": group_openid,
            "content": content,
            "message_type": 0,
            "timestamp": "2026-09-08T15:00:00+08:00",
            "author": {
                "id": "member-1",
                "member_openid": "member-1",
                "username": "小明",
                "member_role": "member",
                "bot": False,
            },
            "attachments": [
                {
                    "content_type": "image/jpeg",
                    "filename": "photo.jpg",
                    "url": "https://example.com/photo.jpg",
                }
            ],
            "message_scene": {
                "source": "default",
                "ext": ["msg_idx=REFIDX_1", "auth_token=secret"],
            },
        },
    }


class GroupMessageStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = f"{self.temp_dir.name}/group_messages.db"
        self.store = GroupMessageStore(self.db_path)
        await self.store.initialize()

    async def asyncTearDown(self):
        await self.store.close()
        self.temp_dir.cleanup()

    async def test_full_event_is_saved_with_complete_payload(self):
        saved = await self.store.save_gateway_event(
            "GROUP_MESSAGE_CREATE", group_event()
        )

        self.assertTrue(saved)
        messages = await self.store.recent("group-1")
        self.assertEqual(len(messages), 1)
        message = messages[0]
        self.assertEqual(message.message_id, "message-1")
        self.assertEqual(message.member_openid, "member-1")
        self.assertEqual(message.username, "小明")
        self.assertEqual(message.content, "大家早上好")
        self.assertEqual(message.payload["attachments"][0]["filename"], "photo.jpg")
        self.assertEqual(
            message.payload["message_scene"]["ext"][0], "msg_idx=REFIDX_1"
        )
        self.assertEqual(os.stat(self.db_path).st_mode & 0o777, 0o600)

    async def test_at_event_and_duplicate_message_are_upserted(self):
        first = group_event(content="旧内容")
        second = group_event(content="更新内容")

        self.assertTrue(
            await self.store.save_gateway_event(
                "GROUP_AT_MESSAGE_CREATE", first
            )
        )
        self.assertTrue(
            await self.store.save_gateway_event(
                "GROUP_MESSAGE_CREATE", second
            )
        )

        self.assertEqual(await self.store.count(), 1)
        message = (await self.store.recent("group-1"))[0]
        self.assertEqual(message.content, "更新内容")
        self.assertEqual(message.event_type, "GROUP_MESSAGE_CREATE")

    async def test_successful_bot_reply_is_saved_as_group_history(self):
        saved = await self.store.save_bot_reply(
            message_id="bot-message-1",
            group_openid="group-1",
            username="浴火AI猫娘助手",
            content="主人，已经处理好啦喵。",
            message_type=2,
            sent_at="2026-09-08T17:40:00+08:00",
            source_message_id="user-message-1",
            payload={
                "id": "bot-message-1",
                "group_openid": "group-1",
                "message_type": 2,
                "markdown": {"content": "主人，已经处理好啦喵。"},
                "author": {"bot": True, "username": "浴火AI猫娘助手"},
            },
        )

        self.assertTrue(saved)
        message = (await self.store.recent("group-1"))[0]
        self.assertTrue(message.is_bot)
        self.assertEqual(message.event_type, "BOT_MESSAGE_CREATE")
        self.assertEqual(message.event_id, "user-message-1")
        self.assertEqual(message.content, "主人，已经处理好啦喵。")
        self.assertEqual(message.message_type, 2)
        self.assertEqual(message.payload["markdown"]["content"], message.content)

    async def test_recent_messages_are_group_scoped_and_paginated(self):
        await self.store.save_gateway_event(
            "GROUP_MESSAGE_CREATE", group_event("message-1")
        )
        await self.store.save_gateway_event(
            "GROUP_MESSAGE_CREATE", group_event("message-2")
        )
        await self.store.save_gateway_event(
            "GROUP_MESSAGE_CREATE",
            group_event("message-3", group_openid="group-2"),
        )

        latest = await self.store.recent("group-1", limit=1)
        older = await self.store.recent(
            "group-1", limit=10, before_record_id=latest[0].record_id
        )

        self.assertEqual([item.message_id for item in latest], ["message-2"])
        self.assertEqual([item.message_id for item in older], ["message-1"])

    async def test_recent_messages_can_exclude_trigger_message(self):
        await self.store.save_gateway_event(
            "GROUP_MESSAGE_CREATE", group_event("message-1")
        )
        await self.store.save_gateway_event(
            "GROUP_AT_MESSAGE_CREATE", group_event("message-2")
        )

        messages = await self.store.recent(
            "group-1", limit=10, exclude_message_id="message-2"
        )

        self.assertEqual([item.message_id for item in messages], ["message-1"])

    async def test_recent_images_only_returns_image_records(self):
        await self.store.save_gateway_event(
            "GROUP_MESSAGE_CREATE", group_event("image-1")
        )
        text_event = group_event("text-1")
        text_event["d"]["attachments"] = []
        await self.store.save_gateway_event("GROUP_MESSAGE_CREATE", text_event)
        await self.store.save_gateway_event(
            "GROUP_MESSAGE_CREATE", group_event("image-2")
        )

        messages = await self.store.recent_images(
            "group-1", exclude_message_id="image-2"
        )

        self.assertEqual([item.message_id for item in messages], ["image-1"])

    async def test_message_can_be_loaded_by_group_and_message_id(self):
        await self.store.save_gateway_event(
            "GROUP_MESSAGE_CREATE", group_event("message-1")
        )
        await self.store.save_gateway_event(
            "GROUP_MESSAGE_CREATE",
            group_event("message-2", group_openid="group-2"),
        )

        message = await self.store.get_message("group-1", "message-1")

        self.assertIsNotNone(message)
        self.assertEqual(message.record_id, 1)
        self.assertIsNone(
            await self.store.get_message("group-2", "message-1")
        )

    async def test_group_summaries_include_latest_message_and_count(self):
        await self.store.save_gateway_event(
            "GROUP_MESSAGE_CREATE", group_event("message-1", content="第一条")
        )
        await self.store.save_gateway_event(
            "GROUP_MESSAGE_CREATE", group_event("message-2", content="第二条")
        )
        await self.store.save_gateway_event(
            "GROUP_MESSAGE_CREATE",
            group_event("message-3", group_openid="group-2"),
        )

        groups = await self.store.groups()

        self.assertEqual([group.group_openid for group in groups], ["group-2", "group-1"])
        self.assertEqual(groups[1].message_count, 2)
        self.assertEqual(groups[1].latest_content, "第二条")

    async def test_invalid_or_unrelated_events_are_ignored(self):
        self.assertFalse(
            await self.store.save_gateway_event("C2C_MESSAGE_CREATE", group_event())
        )
        self.assertFalse(
            await self.store.save_gateway_event(
                "GROUP_MESSAGE_CREATE", {"d": {"id": "missing-group"}}
            )
        )
        self.assertEqual(await self.store.count(), 0)

    def test_database_path_comes_from_environment(self):
        with patch.dict(
            os.environ,
            {"AIQQ_GROUP_MESSAGE_DB": "/tmp/custom-group-messages.db"},
            clear=True,
        ):
            store = GroupMessageStore.from_env()

        self.assertEqual(store.db_path, "/tmp/custom-group-messages.db")


if __name__ == "__main__":
    unittest.main()
