import asyncio
import unittest
from datetime import datetime, timezone

from aiqq.interfaces.qq.handlers import (
    GroupMessageHandler,
    IncomingGroupMessage,
    normalized_group_message_data,
    raw_event_mentions_bot,
)
from aiqq.logic.models import BotSentGroupMessage, ConversationResult


class FakeWorkflow:
    def __init__(self, *, progress=False):
        self.calls = []
        self.progress = progress

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        if self.progress:
            await kwargs["on_progress"]("正在检索第一项。")
            await kwargs["on_progress"]("正在核对第二项。")
        return ConversationResult("ok", "complete answer", "answer")


class FakeSender:
    def __init__(self):
        self.text_calls = []
        self.markdown_calls = []
        self.recalled = []

    async def send_text_reply(self, **kwargs):
        self.text_calls.append(kwargs)
        return BotSentGroupMessage(
            kwargs["group_id"],
            f"progress-{len(self.text_calls)}",
            datetime.now(timezone.utc),
            True,
        )

    async def send_markdown_reply(self, **kwargs):
        self.markdown_calls.append(kwargs)

    async def recall_from_group(self, target):
        self.recalled.append(target.message_id)
        return True


class FakeReplySender:
    def __init__(self):
        self.calls = []
        self.deferred_calls = []
        self.deferred = FakeDeferredReply()

    async def send(self, result, **kwargs):
        self.calls.append((result, kwargs))
        return kwargs["first_msg_seq"] + 1

    async def start_deferred(self, **kwargs):
        self.deferred_calls.append(kwargs)
        return self.deferred


class FakeDeferredReply:
    def __init__(self):
        self.progress = []
        self.result = None
        self.failure = None
        self.finished = asyncio.Event()

    async def publish(self, content):
        self.progress.append(content)

    async def finish(self, result):
        self.result = result
        self.finished.set()

    async def fail(self, content):
        self.failure = content
        self.finished.set()


class SlowWorkflow:
    def __init__(self, *, fail=False):
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False
        self.fail = fail

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        self.started.set()
        try:
            await self.release.wait()
            await kwargs["on_progress"]("后台任务已有新进展。")
            if self.fail:
                raise RuntimeError("late failure")
            return ConversationResult("ok", "后台完整回答", "后台摘要")
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def incoming(message_id="message-1", content="question"):
    return IncomingGroupMessage("group-1", message_id, "member-1", content)


class GroupMessageHandlerTests(unittest.IsolatedAsyncioTestCase):
    def make_handler(self, *, progress=False, workflow=None, timeout=10):
        workflow = workflow or FakeWorkflow(progress=progress)
        sender = FakeSender()
        replies = FakeReplySender()
        handler = GroupMessageHandler(
            workflow=workflow,
            sender=sender,
            reply_sender=replies,
            defer_after_seconds=timeout,
        )
        return handler, workflow, sender, replies

    async def test_progress_is_recalled_and_final_reply_uses_next_sequence(self):
        handler, workflow, sender, replies = self.make_handler(progress=True)

        self.assertTrue(await handler.handle(incoming()))

        self.assertEqual(len(workflow.calls), 1)
        self.assertEqual(sender.recalled, ["progress-1", "progress-2"])
        self.assertEqual(replies.calls[0][1]["first_msg_seq"], 3)

    async def test_duplicate_message_is_processed_once(self):
        handler, workflow, _sender, replies = self.make_handler()
        self.assertTrue(await handler.handle(incoming()))
        self.assertFalse(await handler.handle(incoming()))
        self.assertEqual(len(workflow.calls), 1)
        self.assertEqual(len(replies.calls), 1)

    async def test_slow_workflow_continues_and_finishes_in_deferred_reply(self):
        workflow = SlowWorkflow()
        handler, _workflow, _sender, replies = self.make_handler(
            workflow=workflow, timeout=0.01
        )

        self.assertTrue(await handler.handle(incoming()))

        self.assertFalse(workflow.cancelled)
        self.assertEqual(len(replies.calls), 0)
        self.assertEqual(len(replies.deferred_calls), 1)
        self.assertEqual(replies.deferred_calls[0]["first_msg_seq"], 1)

        workflow.release.set()
        await asyncio.wait_for(replies.deferred.finished.wait(), 0.2)
        self.assertFalse(workflow.cancelled)
        self.assertEqual(replies.deferred.progress, ["后台任务已有新进展。"])
        self.assertEqual(replies.deferred.result.full_text, "后台完整回答")
        self.assertIsNone(replies.deferred.failure)

    async def test_late_workflow_failure_is_written_to_deferred_reply(self):
        workflow = SlowWorkflow(fail=True)
        handler, _workflow, _sender, replies = self.make_handler(
            workflow=workflow, timeout=0.01
        )
        await handler.handle(incoming())

        workflow.release.set()
        await asyncio.wait_for(replies.deferred.finished.wait(), 0.2)

        self.assertFalse(workflow.cancelled)
        self.assertIn("暂时不可用", replies.deferred.failure)

    async def test_menu_bypasses_conversation_and_has_no_removed_buttons(self):
        handler, workflow, sender, _replies = self.make_handler()
        await handler.handle(incoming(content="/菜单"))
        self.assertEqual(workflow.calls, [])
        payload = str(sender.markdown_calls[0]["keyboard"])
        self.assertNotIn("清除记忆", payload)
        self.assertNotIn("GPT生图", payload)

    async def test_removed_clear_command_is_ordinary_user_input(self):
        handler, workflow, _sender, _replies = self.make_handler()
        await handler.handle(incoming(content="清除记忆"))
        self.assertEqual(workflow.calls[0]["user_input"], "清除记忆")

    def test_raw_full_message_requires_explicit_bot_mention(self):
        payload = {
            "d": {
                "content": "@AiQQ hello",
                "mentions": [{"is_you": True, "username": "AiQQ"}],
            }
        }
        self.assertTrue(raw_event_mentions_bot(payload))
        self.assertEqual(normalized_group_message_data(payload)["content"], "hello")
        self.assertFalse(
            raw_event_mentions_bot({"d": {"content": "hello", "mentions": []}})
        )


if __name__ == "__main__":
    unittest.main()
