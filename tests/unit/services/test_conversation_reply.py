import unittest
from types import SimpleNamespace

from aiqq.interfaces.qq.reply import (
    DEFERRED_NOTICE_TEXT,
    FULL_REPLY_UNAVAILABLE_TEXT,
    IMAGE_DELIVERY_FAILED_TEXT,
    ConversationReplySender,
)
from aiqq.logic.models import ConversationResult, ImageAsset, SourceReference


class FakeSender:
    def __init__(self):
        self.markdown_calls = []
        self.image_calls = []
        self.text_calls = []
        self.image_error = None

    async def send_markdown_reply(self, **kwargs):
        self.markdown_calls.append(kwargs)

    async def send_image_reply(self, **kwargs):
        self.image_calls.append(kwargs)
        if self.image_error:
            raise self.image_error

    async def send_text_reply(self, **kwargs):
        self.text_calls.append(kwargs)


class FakeReplyStore:
    def __init__(self, error=None):
        self.saved = []
        self.pending = []
        self.updated = []
        self.error = error

    async def save(self, content):
        self.saved.append(content)
        if self.error:
            raise self.error
        return SimpleNamespace(public_url="https://public.example/reply/token.txt")

    async def create_pending(self, content):
        self.pending.append(content)
        if self.error:
            raise self.error
        return SimpleNamespace(
            file_name="token.txt",
            public_url="https://public.example/reply/token.txt",
        )

    async def update(self, file_name, content, *, pending):
        self.updated.append((file_name, content, pending))
        return True


class FakeMediaStore:
    def __init__(self):
        self.saved = []

    async def save(self, image):
        self.saved.append(image)
        return SimpleNamespace(public_url="https://public.example/media/token.jpg")


class ConversationReplySenderTests(unittest.IsolatedAsyncioTestCase):
    def make_presenter(self, sender=None, reply_store=None):
        sender = sender or FakeSender()
        return (
            ConversationReplySender(
                sender=sender,
                reply_store=reply_store or FakeReplyStore(),
                media_store=FakeMediaStore(),
            ),
            sender,
        )

    async def test_long_reply_sends_summary_link_and_records_complete_answer(self):
        presenter, sender = self.make_presenter()
        result = ConversationResult(
            "ok",
            "很长的完整回答" * 20,
            "回答摘要喵。",
            sources=(SourceReference("来源", "https://source.example/a"),),
        )

        next_sequence = await presenter.send(
            result,
            group_id="group",
            source_message_id="source",
            member_openid="member",
            first_msg_seq=3,
        )

        call = sender.markdown_calls[0]
        self.assertEqual(next_sequence, 4)
        self.assertEqual(call["msg_seq"], 3)
        self.assertIn("<@member> 回答摘要喵。", call["content"])
        self.assertIn("token.txt", str(call["keyboard"]))
        self.assertIn("很长的完整回答", call["record_content"])
        self.assertIn("https://source.example/a", call["record_content"])

    async def test_short_reply_with_long_sources_sends_summary_and_link(self):
        reply_store = FakeReplyStore()
        presenter, sender = self.make_presenter(reply_store=reply_store)
        result = ConversationResult(
            "ok",
            "简短回答。",
            "回答摘要喵。",
            sources=(
                SourceReference(
                    "较长的参考来源",
                    "https://source.example/" + "a" * 80,
                ),
            ),
        )

        await presenter.send(
            result,
            group_id="group",
            source_message_id="source",
            member_openid="member",
            first_msg_seq=3,
        )

        call = sender.markdown_calls[0]
        self.assertEqual(call["content"], "<@member> 回答摘要喵。")
        self.assertIn("token.txt", str(call["keyboard"]))
        self.assertEqual(reply_store.saved, [call["record_content"]])
        self.assertIn("https://source.example/", call["record_content"])

    async def test_deferred_reply_updates_one_file_until_completion(self):
        reply_store = FakeReplyStore()
        presenter, sender = self.make_presenter(reply_store=reply_store)

        deferred = await presenter.start_deferred(
            group_id="group",
            source_message_id="source",
            member_openid="member",
            first_msg_seq=4,
        )
        await deferred.publish("已完成检索。")
        await deferred.finish(
            ConversationResult(
                "ok",
                "最终完整回答。",
                "最终摘要。",
                sources=(SourceReference("来源", "https://source.example/a"),),
            )
        )

        call = sender.markdown_calls[0]
        self.assertEqual(call["content"], f"<@member> {DEFERRED_NOTICE_TEXT}")
        self.assertEqual(call["msg_seq"], 4)
        self.assertIn("token.txt", str(call["keyboard"]))
        self.assertEqual(len(reply_store.pending), 1)
        self.assertEqual(reply_store.updated[0][0], "token.txt")
        self.assertIn("已完成检索。", reply_store.updated[0][1])
        self.assertTrue(reply_store.updated[0][2])
        self.assertEqual(reply_store.updated[1][0], "token.txt")
        self.assertIn("最终完整回答。", reply_store.updated[1][1])
        self.assertIn("https://source.example/a", reply_store.updated[1][1])
        self.assertFalse(reply_store.updated[1][2])

    async def test_full_reply_store_failure_is_visible(self):
        presenter, sender = self.make_presenter(
            reply_store=FakeReplyStore(RuntimeError("disk full"))
        )
        await presenter.send(
            ConversationResult("ok", "long" * 20, "摘要喵。"),
            group_id="group",
            source_message_id="source",
            member_openid="member",
            first_msg_seq=1,
        )
        self.assertIn(FULL_REPLY_UNAVAILABLE_TEXT, sender.markdown_calls[0]["content"])

    async def test_image_send_failure_produces_text_notice(self):
        sender = FakeSender()
        sender.image_error = RuntimeError("QQ unavailable")
        presenter, sender = self.make_presenter(sender=sender)
        await presenter.send(
            ConversationResult(
                "ok",
                "answer",
                "answer",
                images=(ImageAsset(b"image", "image/jpeg", 1, 1),),
            ),
            group_id="group",
            source_message_id="source",
            member_openid="member",
            first_msg_seq=2,
        )
        self.assertEqual(sender.image_calls[0]["msg_seq"], 3)
        self.assertEqual(sender.text_calls[0]["content"], IMAGE_DELIVERY_FAILED_TEXT)


if __name__ == "__main__":
    unittest.main()
