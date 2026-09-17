import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from qq_media_service import upload_qq_image
from recorded_group_message import RecordedGroupMessage


class FakeMessage:
    def __init__(self, response=None):
        self.id = "user-message-1"
        self.group_openid = "group-1"
        self._api = SimpleNamespace(
            post_group_file=AsyncMock(return_value={"file_info": "file-info-1"})
        )
        self.response = (
            {
                "id": "bot-message-1",
                "timestamp": "2026-09-08T17:40:00+08:00",
            }
            if response is None
            else response
        )
        self.reply_calls = []

    async def reply(self, **kwargs):
        self.reply_calls.append(kwargs)
        return self.response


class RecordedGroupMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_markdown_reply_is_recorded(self):
        store = SimpleNamespace(save_bot_reply=AsyncMock(return_value=True))
        source = FakeMessage()
        message = RecordedGroupMessage(
            source,
            store,
            bot_username="浴火AI猫娘助手",
        )

        response = await message.reply(
            msg_type=2,
            markdown={"content": "主人，已经处理好啦喵。"},
            msg_seq=2,
        )

        self.assertEqual(response["id"], "bot-message-1")
        call = store.save_bot_reply.await_args.kwargs
        self.assertEqual(call["message_id"], "bot-message-1")
        self.assertEqual(call["source_message_id"], "user-message-1")
        self.assertEqual(call["content"], "主人，已经处理好啦喵。")
        self.assertEqual(call["message_type"], 2)
        self.assertTrue(call["payload"]["author"]["bot"])
        self.assertEqual(
            call["payload"]["markdown"]["content"],
            "主人，已经处理好啦喵。",
        )

    async def test_uploaded_image_url_is_added_to_recorded_attachment(self):
        store = SimpleNamespace(save_bot_reply=AsyncMock(return_value=True))
        source = FakeMessage()
        message = RecordedGroupMessage(
            source,
            store,
            bot_username="浴火AI猫娘助手",
        )
        public_url = "https://www.firesoul.cn/aiqq/media/result.jpg"

        media = await upload_qq_image(message, public_url)
        await message.reply(msg_type=7, media=media, msg_seq=3)

        call = store.save_bot_reply.await_args.kwargs
        self.assertEqual(call["content"], "[图片]")
        self.assertEqual(
            call["payload"]["attachments"],
            [
                {
                    "content_type": "image/jpeg",
                    "filename": "result.jpg",
                    "url": public_url,
                }
            ],
        )

    async def test_response_without_message_id_is_not_recorded(self):
        store = SimpleNamespace(save_bot_reply=AsyncMock(return_value=True))
        message = RecordedGroupMessage(
            FakeMessage(response={"timestamp": "2026-09-08T17:40:00+08:00"}),
            store,
            bot_username="浴火AI猫娘助手",
        )

        await message.reply(msg_type=2, markdown={"content": "测试"})

        store.save_bot_reply.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
