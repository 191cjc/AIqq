import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from botpy.errors import ServerError

from aiqq.interfaces.qq.handlers import (
    GroupMessageHandler,
    IncomingGroupMessage,
    normalized_group_message_data,
    raw_event_mentions_bot,
)
from aiqq.interfaces.qq.reply import ConversationReplySender
from aiqq.logic.models import BotSentGroupMessage, ConversationResult, ImageAsset


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
    def __init__(self, *, fail=False, images=()):
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False
        self.fail = fail
        self.images = images

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        self.started.set()
        try:
            await self.release.wait()
            await kwargs["on_progress"]("后台任务已有新进展。")
            if self.fail:
                raise RuntimeError("late failure")
            return ConversationResult("ok", "后台完整回答", "后台摘要", images=self.images)
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

    async def test_image_only_request_reaches_workflow_and_same_id_in_another_group_is_distinct(self):
        handler, workflow, sender, replies = self.make_handler()
        for group in ("group-1", "group-2"):
            message = IncomingGroupMessage(group, "same-message-id", "member", "<@bot> ", True)
            self.assertTrue(await handler.handle(message))
            self.assertFalse(await handler.handle(message))
        self.assertEqual(len(workflow.calls), 2)
        self.assertIn("图片", workflow.calls[0]["user_input"])
        self.assertEqual(sender.text_calls, [])
        self.assertEqual(len(replies.calls), 2)

    async def test_final_delivery_failure_is_contained_without_repeating_workflow(self):
        for error_type in (ServerError, RuntimeError, TimeoutError):
            with self.subTest(error_type=error_type.__name__):
                handler, workflow, sender, replies = self.make_handler(progress=True)
                replies.send = AsyncMock(side_effect=error_type(
                    "private-error group-1 message-1 member-1 question complete answer"
                ))

                with self.assertLogs("aiqq.interfaces.qq.handlers", level="WARNING") as logs:
                    self.assertTrue(await handler.handle(incoming()))
                    self.assertFalse(await handler.handle(incoming()))

                self.assertEqual([record.getMessage() for record in logs.records], [
                    f"event=group_reply_delivery_failed error_type={error_type.__name__}"
                ])
                self.assertEqual(len(workflow.calls), 1)
                self.assertEqual(replies.send.await_count, 1)
                self.assertEqual(sender.recalled, ["progress-1", "progress-2"])
                self.assertEqual(replies.send.await_args.kwargs["first_msg_seq"], 3)
                self.assertEqual(handler._deferred_tasks, set())

    async def test_cancellation_during_final_delivery_propagates(self):
        handler, workflow, sender, replies = self.make_handler(progress=True)
        replies.send = AsyncMock(side_effect=asyncio.CancelledError())

        with self.assertNoLogs("aiqq.interfaces.qq.handlers", level="WARNING"):
            with self.assertRaises(asyncio.CancelledError):
                await handler.handle(incoming())

        self.assertEqual(len(workflow.calls), 1)
        self.assertEqual(replies.send.await_count, 1)
        self.assertEqual(sender.recalled, ["progress-1", "progress-2"])
        self.assertEqual(handler._deferred_tasks, set())

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

    async def test_late_workflow_failure_uses_final_deferred_delivery(self):
        workflow = SlowWorkflow(fail=True)
        handler, _workflow, _sender, replies = self.make_handler(
            workflow=workflow, timeout=0.01
        )
        await handler.handle(incoming())

        workflow.release.set()
        await asyncio.wait_for(replies.deferred.finished.wait(), 0.2)

        self.assertFalse(workflow.cancelled)
        self.assertIsNone(replies.deferred.failure)
        self.assertEqual(replies.deferred.result.status, "unavailable")
        self.assertEqual(replies.deferred.result.error_code, "conversation_unavailable")
        self.assertIn("暂时不可用", replies.deferred.result.full_text)

    async def test_failed_deferred_notice_still_sends_late_workflow_failure(self):
        workflow = SlowWorkflow(fail=True)
        sender = FakeSender()
        sender.send_markdown_reply = AsyncMock(
            side_effect=[RuntimeError("notice unavailable"), None]
        )
        store = SimpleNamespace(
            create_pending=AsyncMock(return_value=SimpleNamespace(
                file_name="result.txt",
                public_url="https://public.example/reply/result.txt",
            )),
            update=AsyncMock(return_value=True),
        )
        handler = GroupMessageHandler(
            workflow=workflow,
            sender=sender,
            reply_sender=ConversationReplySender(
                sender=sender,
                reply_store=store,
                media_store=SimpleNamespace(save=AsyncMock()),
            ),
            defer_after_seconds=0.01,
        )

        self.assertTrue(await handler.handle(incoming()))
        workflow.release.set()
        await asyncio.wait_for(asyncio.gather(*handler._deferred_tasks), 0.2)

        self.assertEqual(sender.send_markdown_reply.await_count, 2)
        final_reply = sender.send_markdown_reply.await_args.kwargs
        self.assertEqual(final_reply["group_id"], "group-1")
        self.assertEqual(final_reply["source_message_id"], "message-1")
        self.assertEqual(final_reply["fallback_member_openid"], "member-1")
        self.assertEqual(final_reply["msg_seq"], 2)
        self.assertTrue(final_reply["content"].startswith("<@member-1> "))
        self.assertIn("暂时不可用", final_reply["content"])
        self.assertFalse(store.update.await_args.kwargs["pending"])

    async def test_deferred_send_failure_is_logged_as_delivery_failure(self):
        workflow = SlowWorkflow()
        handler, _workflow, _sender, replies = self.make_handler(
            workflow=workflow, timeout=0.01
        )
        replies.deferred.finish = AsyncMock(side_effect=RuntimeError("QQ unavailable"))
        await handler.handle(incoming())

        with self.assertLogs("aiqq.interfaces.qq.handlers", level="WARNING") as logs:
            workflow.release.set()
            await asyncio.wait_for(asyncio.gather(*handler._deferred_tasks), 0.2)

        self.assertIn("event=deferred_reply_delivery_failed", " ".join(logs.output))
        self.assertNotIn("event=deferred_reply_update_failed", " ".join(logs.output))

    async def test_deferred_page_and_notice_failure_still_deliver_final_images(self):
        image = ImageAsset(b"image", "image/jpeg", 1, 1)
        workflow = SlowWorkflow(images=(image,))
        handler, _workflow, sender, replies = self.make_handler(
            workflow=workflow, timeout=0.01
        )
        replies.start_deferred = AsyncMock(side_effect=RuntimeError("disk full"))
        sender.send_text_reply = AsyncMock(side_effect=RuntimeError("QQ unavailable"))

        self.assertTrue(await handler.handle(incoming()))
        self.assertEqual(len(handler._deferred_tasks), 1)
        self.assertEqual(
            sender.send_text_reply.await_args.kwargs["fallback_member_openid"], "member-1"
        )
        workflow.release.set()
        await asyncio.wait_for(asyncio.gather(*handler._deferred_tasks), 0.2)

        self.assertFalse(workflow.cancelled)
        self.assertEqual(len(replies.calls), 1)
        result, context = replies.calls[0]
        self.assertEqual(result.images, (image,))
        self.assertEqual(context, {
            "group_id": "group-1", "source_message_id": "message-1",
            "member_openid": "member-1", "first_msg_seq": 2,
        })

    async def test_deferred_page_failure_still_reports_late_workflow_failure(self):
        workflow = SlowWorkflow(fail=True)
        handler, _workflow, _sender, replies = self.make_handler(
            workflow=workflow, timeout=0.01
        )
        replies.start_deferred = AsyncMock(side_effect=RuntimeError("disk full"))

        await handler.handle(incoming())
        workflow.release.set()
        await asyncio.wait_for(asyncio.gather(*handler._deferred_tasks), 0.2)

        self.assertEqual(len(replies.calls), 1)
        self.assertIn("暂时不可用", replies.calls[0][0].full_text)

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
