import asyncio
import unittest
from types import SimpleNamespace

from ai_service import AIResult
from bot import AiQQBot


class FakeMessage:
    content = "查询最新接口限制"

    def __init__(self):
        self.reply_calls = []

    async def reply(self, **kwargs):
        self.reply_calls.append(kwargs)
        return {"id": f"reply-{len(self.reply_calls)}"}


class FakeConversations:
    def __init__(self):
        self.summarized = []

    async def chat(self, _key, _prompt, *, on_stage):
        await on_stage("目前资料主要指向接口超时问题。")
        return AIResult("最终完整回答。", True)

    async def summarize_if_needed(self, key):
        self.summarized.append(key)


class BotConversationWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_stage_has_no_buttons_and_final_reply_uses_next_sequence(self):
        bot = SimpleNamespace(
            ai=SimpleNamespace(total_timeout_seconds=270),
            conversations=FakeConversations(),
        )
        message = FakeMessage()

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(len(message.reply_calls), 2)
        stage, final = message.reply_calls
        self.assertEqual(stage["msg_seq"], 1)
        self.assertNotIn("keyboard", stage)
        self.assertEqual(final["msg_seq"], 2)
        self.assertIn("keyboard", final)
        self.assertEqual(bot.conversations.summarized, ["group:1:member:1"])

    async def test_total_timeout_returns_final_error_with_buttons(self):
        class SlowConversations:
            async def chat(self, _key, _prompt, *, on_stage):
                await asyncio.sleep(1)

            async def summarize_if_needed(self, _key):
                raise AssertionError("超时失败不应生成摘要")

        bot = SimpleNamespace(
            ai=SimpleNamespace(total_timeout_seconds=0.01),
            conversations=SlowConversations(),
        )
        message = FakeMessage()

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(len(message.reply_calls), 1)
        final = message.reply_calls[0]
        self.assertEqual(final["msg_seq"], 1)
        self.assertIn("查询时间过长", final["markdown"]["content"])
        self.assertIn("keyboard", final)


if __name__ == "__main__":
    unittest.main()
