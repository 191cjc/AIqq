import tempfile
import unittest

from ai_service import AIResult
from conversation_memory import ConversationManager, ConversationMemory


class FakeAI:
    def __init__(self):
        self.answer_calls = []
        self.summary_calls = []

    async def answer(self, prompt, *, history=(), summary="", on_stage=None):
        self.answer_calls.append(
            {
                "prompt": prompt,
                "history": list(history),
                "summary": summary,
                "on_stage": on_stage,
            }
        )
        return AIResult(f"回答：{prompt}", True)

    async def summarize(self, existing_summary, messages):
        self.summary_calls.append(
            {"existing_summary": existing_summary, "messages": list(messages)}
        )
        return AIResult(f"摘要版本{len(self.summary_calls)}", True)


class ConversationMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.memory = ConversationMemory(
            f"{self.temp_dir.name}/memory.db", summary_every_rounds=10
        )
        await self.memory.initialize()
        self.ai = FakeAI()
        self.manager = ConversationManager(self.ai, self.memory)

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_previous_round_is_sent_to_next_request(self):
        await self.manager.chat("group:1:member:1", "第一问")
        await self.manager.chat("group:1:member:1", "第二问")

        second_call = self.ai.answer_calls[1]
        self.assertEqual(
            second_call["history"],
            [
                {"role": "user", "content": "第一问"},
                {"role": "assistant", "content": "回答：第一问"},
            ],
        )

    async def test_ten_rounds_are_replaced_by_summary(self):
        key = "group:1:member:1"
        for round_number in range(1, 11):
            await self.manager.chat(key, f"第{round_number}问")

        self.assertTrue(await self.manager.summarize_if_needed(key))

        context = await self.memory.load_context(key)
        self.assertEqual(context.summary, "摘要版本1")
        self.assertEqual(context.messages, ())
        self.assertEqual(len(self.ai.summary_calls[0]["messages"]), 20)

        await self.manager.chat(key, "第11问")
        self.assertEqual(self.ai.answer_calls[-1]["summary"], "摘要版本1")
        self.assertEqual(self.ai.answer_calls[-1]["history"], [])

    async def test_each_ten_round_batch_updates_accumulated_summary(self):
        key = "group:1:member:1"
        for batch_number in range(2):
            for round_number in range(10):
                await self.manager.chat(
                    key, f"批次{batch_number + 1}-第{round_number + 1}问"
                )
            self.assertTrue(await self.manager.summarize_if_needed(key))

        self.assertEqual(
            self.ai.summary_calls[1]["existing_summary"], "摘要版本1"
        )
        context = await self.memory.load_context(key)
        self.assertEqual(context.summary, "摘要版本2")
        self.assertEqual(context.messages, ())

    async def test_conversations_are_isolated_and_can_be_cleared(self):
        first_key = "group:1:member:1"
        second_key = "group:1:member:2"
        await self.manager.chat(first_key, "用户一的问题")
        await self.manager.chat(second_key, "用户二的问题")

        second_call = self.ai.answer_calls[1]
        self.assertEqual(second_call["history"], [])

        await self.manager.clear(first_key)
        first_context = await self.memory.load_context(first_key)
        second_context = await self.memory.load_context(second_key)
        self.assertEqual(first_context.messages, ())
        self.assertEqual(len(second_context.messages), 2)

    async def test_stage_callback_is_forwarded_but_only_final_answer_is_stored(self):
        stages = []

        async def collect_stage(message):
            stages.append(message)

        result = await self.manager.chat(
            "group:1:member:1",
            "查询问题",
            on_stage=collect_stage,
        )

        self.assertTrue(result.success)
        self.assertIs(self.ai.answer_calls[0]["on_stage"], collect_stage)
        context = await self.memory.load_context("group:1:member:1")
        self.assertEqual(
            [message.content for message in context.messages],
            ["查询问题", "回答：查询问题"],
        )
        self.assertEqual(stages, [])


if __name__ == "__main__":
    unittest.main()
