import unittest

from message_ui import (
    clear_memory_keyboard,
    reply_research_stage,
    reply_with_clear_memory_button,
)


class FakeMessage:
    def __init__(self):
        self.reply_calls = []

    async def reply(self, **kwargs):
        self.reply_calls.append(kwargs)
        return {"id": "reply-id"}


class MessageUITests(unittest.IsolatedAsyncioTestCase):
    def test_button_inserts_clear_memory_command_for_everyone(self):
        button = clear_memory_keyboard()["content"]["rows"][0]["buttons"][0]

        self.assertEqual(button["render_data"]["label"], "清除记忆")
        self.assertEqual(button["action"]["type"], 2)
        self.assertEqual(button["action"]["permission"]["type"], 2)
        self.assertEqual(button["action"]["data"], "清除记忆")
        self.assertFalse(button["action"]["enter"])

    def test_image_button_is_below_clear_button_and_inserts_command(self):
        rows = clear_memory_keyboard()["content"]["rows"]

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["buttons"][0]["render_data"]["label"], "清除记忆")
        image_button = rows[1]["buttons"][0]
        self.assertEqual(image_button["render_data"]["label"], "NovelAI生图")
        self.assertEqual(image_button["action"]["type"], 2)
        self.assertEqual(image_button["action"]["permission"]["type"], 2)
        self.assertEqual(image_button["action"]["data"], "NovelAI生图")
        self.assertFalse(image_button["action"]["enter"])

    def test_suggestion_button_fills_complete_image_command(self):
        rows = clear_memory_keyboard(
            novelai_suggestion="white cat, morning sunlight"
        )["content"]["rows"]

        self.assertEqual(len(rows), 3)
        button = rows[2]["buttons"][0]
        self.assertEqual(button["render_data"]["label"], "🟢 使用建议")
        self.assertEqual(button["render_data"]["style"], 0)
        self.assertNotEqual(
            button["render_data"]["style"],
            rows[1]["buttons"][0]["render_data"]["style"],
        )
        self.assertEqual(button["action"]["type"], 2)
        self.assertEqual(button["action"]["permission"]["type"], 2)
        self.assertEqual(
            button["action"]["data"],
            "NovelAI生图 white cat, morning sunlight",
        )
        self.assertFalse(button["action"]["enter"])

    async def test_reply_uses_markdown_with_clear_memory_button(self):
        message = FakeMessage()

        response = await reply_with_clear_memory_button(message, "**模型回复**")

        self.assertEqual(response, {"id": "reply-id"})
        self.assertEqual(len(message.reply_calls), 1)
        call = message.reply_calls[0]
        self.assertEqual(call["msg_type"], 2)
        self.assertEqual(call["markdown"], {"content": "**模型回复**"})
        self.assertEqual(call["msg_seq"], 1)
        self.assertEqual(
            call["keyboard"]["content"]["rows"][0]["buttons"][0]["action"][
                "data"
            ],
            "清除记忆",
        )

    async def test_reply_adds_suggestion_button_only_when_requested(self):
        message = FakeMessage()

        await reply_with_clear_memory_button(
            message,
            "建议",
            novelai_suggestion="mountain lake, sunrise",
        )

        rows = message.reply_calls[0]["keyboard"]["content"]["rows"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            rows[2]["buttons"][0]["action"]["data"],
            "NovelAI生图 mountain lake, sunrise",
        )

    async def test_research_stage_uses_markdown_without_keyboard(self):
        message = FakeMessage()

        await reply_research_stage(
            message,
            "目前资料主要指向接口超时问题。",
            msg_seq=3,
        )

        self.assertEqual(
            message.reply_calls,
            [
                {
                    "msg_type": 2,
                    "markdown": {"content": "目前资料主要指向接口超时问题。"},
                    "msg_seq": 3,
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
