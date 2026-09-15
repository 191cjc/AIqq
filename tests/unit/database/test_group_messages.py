import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from aiqq.database.connection import SQLiteConnection
from aiqq.database.group_messages import GroupMessageRepository


def group_event(
    message_id: str,
    *,
    event_type: str = "GROUP_MESSAGE_CREATE",
    group_id: str = "group-1",
    content: str = "hello",
    image: bool = False,
):
    data = {
        "id": message_id,
        "group_openid": group_id,
        "content": content,
        "message_type": 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "author": {
            "member_openid": f"member-{message_id}",
            "username": f"user-{message_id}",
            "member_role": "member",
            "bot": False,
        },
        "attachments": [],
    }
    if image:
        data["attachments"] = [
            {
                "content_type": "image/jpeg",
                "filename": "photo.jpg",
                "url": "https://private.qq.example/photo.jpg",
            }
        ]
    return event_type, {"id": f"event-{message_id}", "d": data}


class GroupMessageRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "group_messages.db"
        self.repository = GroupMessageRepository(SQLiteConnection(self.path))
        await self.repository.initialize()

    async def asyncTearDown(self):
        await self.repository.close()
        self.temporary.cleanup()

    async def add_event(self, message_id, **kwargs):
        event_type, payload = group_event(message_id, **kwargs)
        self.assertTrue(
            await self.repository.add_gateway_event(event_type, payload)
        )

    async def test_reference_contains_non_at_messages_and_bot_final_replies(self):
        await self.add_event("plain", content="not mentioned")
        await self.add_event(
            "mentioned", event_type="GROUP_AT_MESSAGE_CREATE", content="question"
        )
        await self.repository.add_bot_message(
            message_id="bot-final",
            group_openid="group-1",
            username="AiQQ",
            content="final answer",
            message_type=2,
            sent_at="2026-09-09T12:00:01+08:00",
            payload={"content": "final answer"},
            source_message_id="mentioned",
        )
        await self.add_event(
            "current", event_type="GROUP_AT_MESSAGE_CREATE", content="current"
        )

        history = await self.repository.list_for_reference(
            "group-1", before_message_id="current", limit=50
        )

        self.assertEqual(
            [item.content for item in history],
            ["not mentioned", "question", "final answer"],
        )
        self.assertEqual([item.role for item in history], ["user", "user", "assistant"])

    async def test_progress_and_recalled_messages_are_not_reference_material(self):
        await self.add_event("first", image=True)
        await self.repository.add_bot_message(
            message_id="progress",
            group_openid="group-1",
            username="AiQQ",
            content="searching",
            message_type=0,
            sent_at="2026-09-09T12:00:01+08:00",
            payload={"content": "searching"},
            progress=True,
        )
        await self.repository.add_bot_message(
            message_id="recalled",
            group_openid="group-1",
            username="AiQQ",
            content="temporary final",
            message_type=0,
            sent_at="2026-09-09T12:00:02+08:00",
            payload={"content": "temporary final"},
        )
        self.assertTrue(
            await self.repository.mark_recalled(
                group_id="group-1", message_id="recalled"
            )
        )

        history = await self.repository.list_for_reference(
            "group-1", before_message_id="", limit=50
        )

        self.assertEqual([item.content for item in history], ["hello"])
        self.assertTrue(history[0].has_image)

    async def test_reference_image_urls_are_group_scoped_and_include_quotes(self):
        event_type, payload = group_event(
            "image-message", group_id="group-a", image=True
        )
        payload["d"]["msg_elements"] = [
            {
                "attachments": [
                    {
                        "content_type": "image/jpeg",
                        "url": "https://images.example/quoted.jpg",
                    },
                    {
                        "content_type": "text/plain",
                        "url": "https://images.example/not-image.txt",
                    },
                ]
            }
        ]
        await self.repository.add_gateway_event(event_type, payload)
        record = (await self.repository.recent("group-a"))[0]

        urls = await self.repository.list_image_urls("group-a", record.record_id)

        self.assertEqual(
            urls,
            (
                "https://private.qq.example/photo.jpg",
                "https://images.example/quoted.jpg",
            ),
        )
        self.assertEqual(
            await self.repository.list_image_urls("group-b", record.record_id), ()
        )

    async def test_recall_ownership_is_group_scoped_and_bot_only(self):
        await self.add_event("user-message")
        await self.repository.add_bot_message(
            message_id="bot-message",
            group_openid="group-1",
            username="AiQQ",
            content="reply",
            message_type=0,
            sent_at="2026-09-09T12:00:01+08:00",
            payload={"content": "reply"},
        )
        self.assertFalse(
            await self.repository.is_owned_bot_message(
                group_id="group-1", message_id="user-message"
            )
        )
        self.assertFalse(
            await self.repository.is_owned_bot_message(
                group_id="other-group", message_id="bot-message"
            )
        )
        self.assertTrue(
            await self.repository.is_owned_bot_message(
                group_id="group-1", message_id="bot-message"
            )
        )

    async def test_filtering_happens_before_fifty_message_limit(self):
        await self.add_event("valid-old", content="must remain")
        for index in range(60):
            await self.repository.add_bot_message(
                message_id=f"progress-{index}",
                group_openid="group-1",
                username="AiQQ",
                content="temporary",
                message_type=0,
                sent_at="2026-09-09T12:00:01+08:00",
                payload={"content": "temporary"},
                progress=True,
            )
        history = await self.repository.list_for_reference(
            "group-1", before_message_id="", limit=50
        )
        self.assertEqual([item.content for item in history], ["must remain"])

    async def test_console_context_only_uses_recent_mentions_and_free_sequence(self):
        await self.add_event("plain")
        await self.add_event("mention", event_type="GROUP_AT_MESSAGE_CREATE")
        await self.repository.add_bot_message(
            message_id="first-reply",
            group_openid="group-1",
            username="AiQQ",
            content="reply",
            message_type=0,
            sent_at=datetime.now(timezone.utc).isoformat(),
            payload={"msg_seq": 1},
            source_message_id="mention",
        )

        context = await self.repository.find_console_reply_context("group-1")

        self.assertIsNotNone(context)
        self.assertEqual(context.message_id, "mention")
        self.assertEqual(context.next_sequence, 2)

    async def test_console_context_rejects_non_mention_explicit_message(self):
        await self.add_event("plain")
        context = await self.repository.find_console_reply_context(
            "group-1", message_id="plain"
        )
        self.assertIsNone(context)

    async def test_groups_are_sorted_by_latest_message(self):
        await self.add_event("group-one", group_id="group-1", content="older")
        await self.add_event("group-two", group_id="group-2", content="newer")

        groups = await self.repository.groups()

        self.assertEqual(
            [group.group_openid for group in groups], ["group-2", "group-1"]
        )
        self.assertEqual(groups[0].latest_content, "newer")
        self.assertEqual(groups[0].message_count, 1)


if __name__ == "__main__":
    unittest.main()
