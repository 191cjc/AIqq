import asyncio
import hashlib
import tempfile
import threading
import unittest
from io import BytesIO
from itertools import product
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

from PIL import Image
from botpy.errors import ServerError

from aiqq.interfaces.qq.reply import (
    DEFERRED_NOTICE_TEXT,
    FULL_REPLY_UNAVAILABLE_TEXT,
    IMAGE_DELIVERY_FAILED_TEXT,
    ConversationReplySender,
)
from aiqq.logic.models import ConversationResult, ImageAsset, SourceReference
from aiqq.services.images.qq_upload import MAX_QQ_IMAGE_BYTES, prepare_qq_image
from aiqq.services.qq.sender import QQMessageSender
from aiqq.services.storage import TemporaryMediaStore


class FakeSender:
    def __init__(self):
        self.markdown_calls = []
        self.image_calls = []
        self.text_calls = []
        self.image_error = None
        self.markdown_error = None
        self.delivery_order = []

    async def send_markdown_reply(self, **kwargs):
        self.delivery_order.append("markdown")
        self.markdown_calls.append(kwargs)
        if self.markdown_error:
            raise self.markdown_error

    async def send_image_reply(self, **kwargs):
        self.delivery_order.append("image")
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
    def make_presenter(self, sender=None, reply_store=None, media_store=None):
        sender = sender or FakeSender()
        return (
            ConversationReplySender(
                sender=sender,
                reply_store=reply_store or FakeReplyStore(),
                media_store=media_store or FakeMediaStore(),
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
        self.assertEqual(call["fallback_member_openid"], "member")

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
        self.assertEqual(len(sender.markdown_calls), 2)
        self.assertEqual(sender.markdown_calls[1]["msg_seq"], 5)
        self.assertEqual(sender.markdown_calls[1]["fallback_member_openid"], "member")
        self.assertEqual(reply_store.saved, [])

    async def test_expired_completed_reply_sends_mention_and_image_once(self):
        errors = ("msgid已经过期,不能回复", "回复消息msg_id已过期")
        for error_message, is_deferred in product(errors, (False, True)):
            with self.subTest(error=error_message, deferred=is_deferred):
                outcomes = [
                    ServerError(error_message), {"id": "markdown"},
                    ServerError(error_message), {"id": "image"},
                ]
                if is_deferred:
                    outcomes[:0] = [ServerError(error_message), {"id": "notice"}]
                api = SimpleNamespace(
                    post_group_message=AsyncMock(side_effect=outcomes),
                    post_group_file=AsyncMock(return_value={"file_info": "uploaded-file"}),
                )
                repository = SimpleNamespace(
                    add_bot_message=AsyncMock(return_value=True),
                    record_delivery_attempt=AsyncMock(return_value=1),
                )
                sender = QQMessageSender(api, repository, bot_username="AiQQ")
                media_store = FakeMediaStore()
                reply_store = FakeReplyStore()
                presenter, _sender = self.make_presenter(
                    sender=sender, media_store=media_store, reply_store=reply_store,
                )
                with Image.new("RGB", (16, 8), "blue") as source:
                    output = BytesIO()
                    source.save(output, format="PNG")
                image = ImageAsset(output.getvalue(), "image/png", 16, 8)
                result = ConversationResult("ok", "answer", "answer", images=(image,))
                context = {
                    "group_id": "group", "source_message_id": "expired",
                    "member_openid": "member", "first_msg_seq": 2,
                }

                with patch(
                    "aiqq.interfaces.qq.reply.prepare_qq_image", wraps=prepare_qq_image
                ) as prepare:
                    if is_deferred:
                        deferred = await presenter.start_deferred(**context)
                        await deferred.finish(result)
                    else:
                        self.assertEqual(await presenter.send(result, **context), 4)

                prepare.assert_called_once_with(image)
                self.assertEqual(media_store.saved, [image])
                self.assertEqual(api.post_group_file.await_count, 1)
                self.assertEqual(api.post_group_message.await_count, 6 if is_deferred else 4)
                calls = api.post_group_message.await_args_list
                original_markdown, markdown, original_media, media = (
                    call.kwargs for call in calls[-4:]
                )
                self.assertEqual(original_markdown["msg_seq"], 3 if is_deferred else 2)
                self.assertEqual(original_media["msg_seq"], 4 if is_deferred else 3)
                self.assertEqual(markdown["markdown"]["content"], "<@member> answer")
                self.assertNotIn("content", original_media)
                self.assertNotIn("content", media)
                self.assertEqual(media["media"], {"file_info": "uploaded-file"})
                self.assertEqual(media["media"], original_media["media"])
                self.assertEqual(markdown["keyboard"], original_markdown["keyboard"])
                fallbacks = [markdown, media]
                if is_deferred:
                    notice = calls[1].kwargs
                    self.assertEqual(
                        notice["markdown"]["content"], f"<@member> {DEFERRED_NOTICE_TEXT}"
                    )
                    self.assertIn("token.txt", str(notice["keyboard"]))
                    self.assertEqual(reply_store.updated, [("token.txt", "answer", False)])
                    fallbacks.append(notice)
                for payload in fallbacks:
                    self.assertEqual(payload["group_openid"], "group")
                    self.assertIsNone(payload["msg_id"])
                    self.assertIsNone(payload["msg_seq"])
                saved = repository.add_bot_message.await_args_list
                self.assertEqual(len(saved), 3 if is_deferred else 2)
                self.assertEqual(saved[-2].kwargs["content"], "answer")
                self.assertEqual(saved[-1].kwargs["content"], "[图片]")
                self.assertEqual(saved[-1].kwargs["payload"]["content"], "[图片]")
                self.assertEqual(saved[-1].kwargs["payload"]["attachments"][0]["content_type"], "image/png")
                for call in saved:
                    self.assertEqual(call.kwargs["source_message_id"], "expired")
                    self.assertEqual(call.kwargs["payload"]["delivery_mode"], "proactive")
                    self.assertEqual(call.kwargs["payload"]["fallback_reason"], "expired_msg_id")

    async def test_images_are_saved_even_when_text_delivery_fails(self):
        sender = FakeSender()
        sender.markdown_error = ServerError("proactive quota exceeded")
        with tempfile.TemporaryDirectory() as directory:
            store = TemporaryMediaStore(Path(directory), "https://public.example", 60)
            await store.initialize()
            presenter, _sender = self.make_presenter(sender=sender, media_store=store)
            with Image.new("RGB", (16, 8), "blue") as source:
                output = BytesIO()
                source.save(output, format="PNG")
            image = ImageAsset(output.getvalue(), "image/png", 16, 8)

            with self.assertRaises(ServerError):
                await presenter.send(
                    ConversationResult("ok", "answer", "answer", images=(image,)),
                    group_id="group", source_message_id="source", member_openid="member",
                    first_msg_seq=2,
                )

            stored_images = list(Path(directory).glob("*.png"))
            self.assertEqual(len(stored_images), 1)
            self.assertEqual(stored_images[0].read_bytes(), image.data)
            self.assertEqual(sender.image_calls, [])
            self.assertEqual(sender.text_calls, [])

    async def test_deferred_completion_preserves_images_and_original_context(self):
        reply_store = FakeReplyStore()
        media_store = FakeMediaStore()
        presenter, sender = self.make_presenter(
            reply_store=reply_store, media_store=media_store
        )
        deferred = await presenter.start_deferred(
            group_id="group", source_message_id="expired", member_openid="member",
            first_msg_seq=4,
        )
        image = ImageAsset(b"image", "image/jpeg", 1, 1)
        await deferred.finish(
            ConversationResult("ok", "最终回答" * 30, "最终摘要", images=(image,))
        )

        self.assertEqual(media_store.saved, [image])
        self.assertFalse(reply_store.updated[-1][2])
        self.assertEqual(reply_store.saved, [])
        final_text = sender.markdown_calls[1]
        self.assertEqual(final_text["content"], "<@member> 最终摘要")
        self.assertEqual(final_text["msg_seq"], 5)
        self.assertIn("token.txt", str(final_text["keyboard"]))
        self.assertEqual(sender.image_calls[0]["msg_seq"], 6)
        for call in (sender.markdown_calls[0], final_text, sender.image_calls[0]):
            self.assertEqual(call["group_id"], "group")
            self.assertEqual(call["source_message_id"], "expired")
            self.assertEqual(call["fallback_member_openid"], "member")

    async def test_deferred_completion_sends_images_when_page_update_fails(self):
        reply_store = FakeReplyStore()
        reply_store.update = AsyncMock(return_value=False)
        presenter, sender = self.make_presenter(reply_store=reply_store)
        deferred = await presenter.start_deferred(
            group_id="group", source_message_id="source", member_openid="member",
            first_msg_seq=4,
        )
        await deferred.finish(ConversationResult(
            "ok", "最终回答" * 30, "最终摘要",
            images=(ImageAsset(b"image", "image/jpeg", 1, 1),),
        ))

        self.assertEqual(len(sender.markdown_calls), 2)
        self.assertEqual(len(sender.image_calls), 1)
        self.assertEqual(reply_store.saved, ["最终回答" * 30])

    async def test_deferred_notice_failure_does_not_discard_final_result(self):
        sender = FakeSender()
        sender.markdown_error = ServerError("proactive quota exceeded")
        presenter, _sender = self.make_presenter(sender=sender)
        deferred = await presenter.start_deferred(
            group_id="group", source_message_id="source", member_openid="member",
            first_msg_seq=4,
        )
        sender.markdown_error = None
        await deferred.finish(ConversationResult(
            "ok", "answer", "answer",
            images=(ImageAsset(b"image", "image/jpeg", 1, 1),),
        ))
        self.assertEqual(sender.markdown_calls[1]["msg_seq"], 5)
        self.assertEqual(sender.image_calls[0]["msg_seq"], 6)

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
        self.assertEqual(sender.text_calls[0]["fallback_member_openid"], "member")

    async def test_large_image_is_stored_as_jpeg_before_qq_upload(self):
        with Image.new("RGB", (1200, 800), "blue") as source:
            output = BytesIO()
            source.save(output, format="PNG", compress_level=0)
        image = ImageAsset(output.getvalue(), "image/png", 1200, 800)
        self.assertGreater(len(image.data), MAX_QQ_IMAGE_BYTES)
        with tempfile.TemporaryDirectory() as directory:
            store = TemporaryMediaStore(Path(directory), "https://public.example", 60)
            await store.initialize()
            presenter, sender = self.make_presenter(media_store=store)

            next_sequence = await presenter.send(
                ConversationResult("ok", "answer", "answer", images=(image,)),
                group_id="group",
                source_message_id="source",
                member_openid="member",
                first_msg_seq=2,
            )

            self.assertEqual(next_sequence, 4)
            self.assertEqual(sender.delivery_order, ["markdown", "image"])
            call = sender.image_calls[0]
            self.assertEqual(call["msg_seq"], 3)
            self.assertEqual(call["mime_type"], "image/jpeg")
            file_name = Path(urlsplit(call["public_url"]).path).name
            self.assertTrue(file_name.endswith(".jpg"))
            stored_path = await store.resolve(file_name)
            self.assertIsNotNone(stored_path)
            self.assertLessEqual(stored_path.stat().st_size, MAX_QQ_IMAGE_BYTES)
            metadata = call["image_metadata"]
            self.assertTrue(metadata["compressed"])
            self.assertEqual(metadata["source"]["size"], len(image.data))
            self.assertEqual(metadata["source"]["sha256"], hashlib.sha256(image.data).hexdigest())
            self.assertEqual(metadata["delivery"]["size"], stored_path.stat().st_size)
            self.assertEqual(metadata["delivery"]["sha256"], hashlib.sha256(stored_path.read_bytes()).hexdigest())
            self.assertEqual(metadata["source"]["content_type"], "image/png")
            self.assertEqual(metadata["delivery"]["content_type"], "image/jpeg")
            with Image.open(stored_path) as decoded:
                decoded.load()
                self.assertEqual(decoded.format, "JPEG")
                self.assertEqual(decoded.size, (1200, 800))
            self.assertEqual(sender.text_calls, [])

    async def test_small_image_is_stored_without_changes(self):
        media_store = FakeMediaStore()
        presenter, sender = self.make_presenter(media_store=media_store)
        with Image.new("RGB", (16, 8), "blue") as source:
            output = BytesIO()
            source.save(output, format="PNG")
        image = ImageAsset(output.getvalue(), "image/png", 16, 8)

        await presenter.send(
            ConversationResult("ok", "answer", "answer", images=(image,)),
            group_id="group",
            source_message_id="source",
            member_openid="member",
            first_msg_seq=2,
        )

        self.assertIs(media_store.saved[0], image)
        self.assertEqual(sender.image_calls[0]["mime_type"], "image/png")

    async def test_compression_failure_prevents_storage_and_upload(self):
        media_store = FakeMediaStore()
        presenter, sender = self.make_presenter(media_store=media_store)
        image = ImageAsset(b"x" * (MAX_QQ_IMAGE_BYTES + 1), "image/png", 1, 1)

        next_sequence = await presenter.send(
            ConversationResult("ok", "answer", "answer", images=(image,)),
            group_id="group",
            source_message_id="source",
            member_openid="member",
            first_msg_seq=2,
        )

        self.assertEqual(next_sequence, 4)
        self.assertEqual(media_store.saved, [])
        self.assertEqual(sender.image_calls, [])
        self.assertEqual(sender.text_calls[0]["content"], IMAGE_DELIVERY_FAILED_TEXT)
        self.assertEqual(sender.text_calls[0]["msg_seq"], 3)

    async def test_cancellation_during_background_preparation_prevents_upload(self):
        media_store = FakeMediaStore()
        presenter, sender = self.make_presenter(media_store=media_store)
        image = ImageAsset(b"image", "image/jpeg", 1, 1)
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = threading.Event()
        finished = threading.Event()
        worker_threads = []

        def blocked_prepare(asset):
            worker_threads.append(threading.get_ident())
            loop.call_soon_threadsafe(started.set)
            try:
                release.wait(timeout=2)
                return asset
            finally:
                finished.set()

        with patch("aiqq.interfaces.qq.reply.prepare_qq_image", blocked_prepare):
            task = asyncio.create_task(presenter.send(
                ConversationResult("ok", "answer", "answer", images=(image,)),
                group_id="group",
                source_message_id="source",
                member_openid="member",
                first_msg_seq=2,
            ))
            try:
                await asyncio.wait_for(started.wait(), timeout=1)
                self.assertNotEqual(worker_threads[0], threading.get_ident())
                self.assertFalse(finished.is_set())
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self.assertTrue(await asyncio.to_thread(finished.wait, 2))

        self.assertEqual(media_store.saved, [])
        self.assertEqual(sender.image_calls, [])
        self.assertEqual(sender.text_calls, [])


if __name__ == "__main__":
    unittest.main()
