import asyncio
import hashlib
import os
import tempfile
import threading
import time
import unittest
from io import BytesIO
from itertools import product
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

from PIL import Image
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from botpy.api import BotAPI
from botpy.errors import ServerError
from botpy.http import _handle_response

from aiqq.interfaces.qq.reply import (
    DEFERRED_NOTICE_TEXT,
    FULL_REPLY_UNAVAILABLE_TEXT,
    IMAGE_CONTENT_REJECTED_TEXT,
    IMAGE_DELIVERY_FAILED_TEXT,
    IMAGE_VIEW_NOTICE_TEXT,
    NOVELAI_TRANSLATION_NOTICE_TEXT,
    ConversationReplySender,
)
from aiqq.interfaces.web.media import MediaHandler, register_media_routes
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
        self.recall_status = "recalled"
        self.recall_calls = []

    async def recall_member_message(self, **kwargs):
        self.delivery_order.append("recall")
        self.recall_calls.append(kwargs)
        return self.recall_status

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
        self.delivery_order.append("text")
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


class ImageRejectionHttp:
    """Drive actual SDK serialization/error parsing without a live QQ request."""

    def __init__(self, stage, *, expiry=None):
        self.stage = stage
        self.expiry = expiry
        self.payloads = []
        self.uploads = []
        self.image_sends = 0

    async def request(self, route, **kwargs):
        payload = kwargs["json"]
        if route.path.endswith("/files"):
            self.uploads.append(payload)
            if self.stage == "upload":
                return await self.reject(route, 40034006, "消息内容违规")
            return {"file_info": "uploaded-once"}
        self.payloads.append(payload)
        if payload["msg_type"] == 7:
            self.image_sends += 1
            if self.expiry is not None and self.image_sends == 1:
                return await self.reject(route, *self.expiry)
            return await self.reject(route, 40034006, "消息内容违规")
        return {"id": f"text-{len(self.payloads)}"}

    @staticmethod
    async def reject(route, code, message):
        return await _handle_response(SimpleNamespace(
            status=400,
            request_info=SimpleNamespace(url=route.url),
            headers={"content-type": "application/json"},
            json=AsyncMock(return_value={"message": message, "code": code, "err_code": code}),
        ))


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

    async def test_novelai_translation_notice_quotes_original_message(self):
        presenter, sender = self.make_presenter()

        await presenter.send_novelai_translation_start(
            group_id="group", source_message_id="source",
            member_openid="member", msg_seq=4,
        )

        self.assertEqual(sender.text_calls, [{
            "group_id": "group", "source_message_id": "source",
            "content": NOVELAI_TRANSLATION_NOTICE_TEXT, "msg_seq": 4,
            "fallback_member_openid": "member", "quote_source": True,
        }])

    async def test_receipt_recalls_original_and_confirms_in_one_message(self):
        presenter, sender = self.make_presenter()
        await presenter.acknowledge_image_request(
            group_id="group", source_message_id="source",
            member_openid="member", msg_seq=1, translate_prompt=True,
        )
        self.assertEqual(sender.delivery_order, ["recall", "text"])
        self.assertEqual(sender.recall_calls, [{"group_id": "group", "message_id": "source"}])
        self.assertEqual(len(sender.text_calls), 1)
        notice = sender.text_calls[0]
        self.assertIn("已收到提示词，原消息已撤回。", notice["content"])
        self.assertIn("正在转换为英文提示词", notice["content"])
        self.assertEqual(notice["msg_seq"], 1)
        self.assertFalse(notice.get("quote_source", False))

    async def test_failed_recall_never_claims_success_and_keeps_one_receipt(self):
        for status, reason in (
            ("permission_denied", "群管理员"),
            ("expired", "超过两分钟"), ("failed", "撤回失败"),
        ):
            with self.subTest(status=status):
                presenter, sender = self.make_presenter()
                sender.recall_status = status
                await presenter.acknowledge_image_request(
                    group_id="group", source_message_id="source",
                    member_openid="member", msg_seq=1,
                )
                self.assertEqual(len(sender.text_calls), 1)
                content = sender.text_calls[0]["content"]
                self.assertIn("已收到提示词", content)
                self.assertIn(reason, content)
                self.assertIn("正在处理作画请求", content)
                self.assertNotIn("已撤回", content)

    async def test_novelai_result_links_to_literal_copyable_scene_prompt(self):
        reply_store = FakeReplyStore()
        presenter, sender = self.make_presenter(reply_store=reply_store)
        image = ImageAsset(b"image", "image/jpeg", 1, 1)
        result = ConversationResult(
            "ok", "NovelAI 图片已经生成完成。", "NovelAI 图片已经生成完成。",
            images=(image,), generation_prompt="white cat, daylight",
        )

        next_sequence = await presenter.send(
            result, group_id="group", source_message_id="source",
            member_openid="member", first_msg_seq=3,
            show_generation_prompt=True,
        )

        self.assertEqual(reply_store.saved, ["white cat, daylight"])
        self.assertEqual(next_sequence, 5)
        self.assertEqual(sender.delivery_order, ["markdown", "image"])
        keyboard = sender.markdown_calls[0]["keyboard"]
        button = keyboard["content"]["rows"][-1]["buttons"][0]
        self.assertEqual(button["id"], "view_generation_prompt")
        self.assertEqual(button["action"]["data"], "https://public.example/reply/token.txt?view=prompt")
        self.assertEqual(sender.image_calls[0]["msg_seq"], 4)

    async def test_generation_prompt_button_requires_image_route_and_completed_image(self):
        reply_store = FakeReplyStore()
        presenter, sender = self.make_presenter(reply_store=reply_store)
        result = ConversationResult(
            "ok", "done", "done",
            images=(ImageAsset(b"image", "image/jpeg", 1, 1),),
            generation_prompt="white cat",
        )
        context = dict(
            group_id="group", source_message_id="source",
            member_openid="member", first_msg_seq=1,
        )

        await presenter.send(result, **context)
        await presenter.send(
            ConversationResult("unavailable", "failed", "failed", error_code="failed"),
            show_generation_prompt=True, **context,
        )

        self.assertEqual(reply_store.saved, [])
        self.assertTrue(all(
            len(call["keyboard"]["content"]["rows"]) == 1
            for call in sender.markdown_calls
        ))

    async def test_generation_prompt_store_failure_does_not_block_image(self):
        reply_store = FakeReplyStore(RuntimeError("private prompt and path"))
        presenter, sender = self.make_presenter(reply_store=reply_store)
        result = ConversationResult(
            "ok", "done", "done",
            images=(ImageAsset(b"image", "image/jpeg", 1, 1),),
            generation_prompt="watercolor, white cat",
        )

        with self.assertLogs("aiqq.interfaces.qq.reply", "WARNING") as logs:
            await presenter.send(
                result, group_id="group", source_message_id="source",
                member_openid="member", first_msg_seq=1,
                show_generation_prompt=True,
            )

        self.assertEqual(len(sender.markdown_calls), 1)
        self.assertEqual(len(sender.image_calls), 1)
        self.assertEqual(len(sender.markdown_calls[0]["keyboard"]["content"]["rows"]), 1)
        self.assertIn("event=generation_prompt_store_failed", logs.output[0])
        self.assertNotIn("watercolor", "\n".join(logs.output))
        self.assertNotIn("private prompt", "\n".join(logs.output))

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

    async def test_image_send_failure_produces_image_link_and_preserves_prompt_link(self):
        sender = FakeSender()
        sender.image_error = RuntimeError("QQ unavailable")
        media_store = FakeMediaStore()
        presenter, sender = self.make_presenter(sender=sender, media_store=media_store)
        next_sequence = await presenter.send(
            ConversationResult(
                "ok",
                "answer",
                "answer",
                images=(ImageAsset(b"image", "image/jpeg", 1, 1),),
                generation_prompt="white cat, {blue eyes}",
            ),
            group_id="group",
            source_message_id="source",
            member_openid="member",
            first_msg_seq=2,
            full_reply_url="https://public.example/reply/full.txt",
            show_generation_prompt=True,
        )
        self.assertEqual(next_sequence, 4)
        self.assertEqual(len(media_store.saved), 1)
        self.assertEqual(sender.delivery_order, ["markdown", "image", "markdown"])
        self.assertEqual(sender.image_calls[0]["msg_seq"], 3)
        self.assertEqual(sender.text_calls, [])
        notice = sender.markdown_calls[-1]
        self.assertEqual(notice["msg_seq"], 3)
        self.assertEqual(notice["group_id"], "group")
        self.assertEqual(notice["source_message_id"], "source")
        self.assertEqual(notice["fallback_member_openid"], "member")
        self.assertEqual(notice["content"], f"<@member> {IMAGE_DELIVERY_FAILED_TEXT}\n\n{IMAGE_VIEW_NOTICE_TEXT}")
        buttons = {row["buttons"][0]["id"]: row["buttons"][0]
                   for row in notice["keyboard"]["content"]["rows"]}
        self.assertEqual(buttons["view_image"]["action"]["data"], sender.image_calls[0]["public_url"])
        self.assertEqual(buttons["view_generation_prompt"]["action"]["data"],
                         "https://public.example/reply/token.txt?view=prompt")
        self.assertEqual(buttons["view_full_reply"]["action"]["data"],
                         "https://public.example/reply/full.txt")

    async def test_sdk_content_rejection_reaches_foreground_and_deferred_user_copy(self):
        stages = (
            ("upload", None),
            ("send", None),
            ("send", (40034031, "msgid已经过期,不能回复")),
            ("send", (40034005, "回复消息msg_id已过期")),
        )
        for (stage, expiry), is_deferred in product(stages, (False, True)):
            with self.subTest(stage=stage, expiry=expiry, deferred=is_deferred):
                http = ImageRejectionHttp(stage, expiry=expiry)
                repository = SimpleNamespace(
                    add_bot_message=AsyncMock(return_value=True),
                    record_delivery_attempt=AsyncMock(return_value=1),
                )
                sender = QQMessageSender(BotAPI(http), repository, bot_username="AiQQ")
                presenter, _sender = self.make_presenter(sender=sender)
                image = ImageAsset(b"image", "image/jpeg", 1, 1)
                result = ConversationResult("ok", "answer", "answer", images=(image,))
                context = dict(group_id="group", source_message_id="source",
                               member_openid="member", first_msg_seq=2)

                with self.assertLogs("aiqq.interfaces.qq.reply", level="WARNING") as logs:
                    if is_deferred:
                        deferred = await presenter.start_deferred(**context)
                        await deferred.finish(result)
                    else:
                        self.assertEqual(await presenter.send(result, **context), 4)

                self.assertEqual([record.getMessage() for record in logs.records], [
                    "event=qq_image_delivery_failed error_kind=content_rejected"
                ])
                self.assertEqual(len(http.uploads), 1)
                expected_image_sends = 0 if stage == "upload" else (2 if expiry else 1)
                self.assertEqual(http.image_sends, expected_image_sends)
                self.assertEqual(len(http.payloads), expected_image_sends + 2 + int(is_deferred))
                notice = http.payloads[-1]
                self.assertEqual(notice["msg_type"], 2)
                self.assertEqual(notice["markdown"]["content"],
                                 f"<@member> {IMAGE_CONTENT_REJECTED_TEXT}\n\n{IMAGE_VIEW_NOTICE_TEXT}")
                image_button = notice["keyboard"]["content"]["rows"][-1]["buttons"][0]
                self.assertEqual(image_button["render_data"]["label"], "查看图片")
                self.assertEqual(image_button["action"]["type"], 0)
                self.assertEqual(image_button["action"]["data"], http.uploads[0]["url"])
                self.assertEqual(notice["group_openid"], "group")
                self.assertEqual(notice["msg_id"], "source")
                self.assertEqual(notice["msg_seq"], 4 if is_deferred else 3)
                image_payloads = [p for p in http.payloads if p["msg_type"] == 7]
                if expiry:
                    self.assertEqual(image_payloads[0]["media"], image_payloads[1]["media"])
                    self.assertIsNone(image_payloads[1]["msg_id"])
                    self.assertIsNone(image_payloads[1]["msg_seq"])
                for payload in image_payloads:
                    self.assertIsNone(payload["content"])
                saved = [call.kwargs for call in repository.add_bot_message.await_args_list]
                self.assertEqual(len(saved), 2 + int(is_deferred))
                self.assertTrue(all(record["message_type"] != 7 for record in saved))
                self.assertEqual(saved[-1]["content"],
                                 f"{IMAGE_CONTENT_REJECTED_TEXT}\n\n{IMAGE_VIEW_NOTICE_TEXT}")
                self.assertEqual(saved[-1]["source_message_id"], "source")
                failures = [
                    call.kwargs for call in repository.record_delivery_attempt.await_args_list
                    if call.kwargs["status"] == "failed"
                ]
                self.assertEqual(len(failures), 2 if expiry else 1)
                self.assertTrue(all(call["error_type"] == "ServerError" for call in failures))

    async def test_unclassified_image_errors_keep_generic_copy_and_cancel_propagates(self):
        for error in (ServerError("消息内容违规; private details"), RuntimeError("消息内容违规"),
                      TimeoutError("private failure"), asyncio.CancelledError()):
            with self.subTest(error=repr(error)):
                sender = FakeSender()
                sender.image_error = error
                presenter, _sender = self.make_presenter(sender=sender)
                call = presenter.send(
                    ConversationResult("ok", "answer", "answer", images=(
                        ImageAsset(b"image", "image/jpeg", 1, 1),
                    )),
                    group_id="group", source_message_id="source", member_openid="member",
                    first_msg_seq=2,
                )
                if isinstance(error, asyncio.CancelledError):
                    with self.assertRaises(asyncio.CancelledError):
                        await call
                    self.assertEqual(sender.text_calls, [])
                    self.assertEqual(len(sender.markdown_calls), 1)
                else:
                    with self.assertLogs("aiqq.interfaces.qq.reply", level="WARNING") as logs:
                        self.assertEqual(await call, 4)
                    self.assertEqual(sender.text_calls, [])
                    self.assertIn(IMAGE_DELIVERY_FAILED_TEXT, sender.markdown_calls[-1]["content"])
                    self.assertIn("view_image", str(sender.markdown_calls[-1]["keyboard"]))
                    self.assertNotIn("private", " ".join(logs.output))
                self.assertEqual(len(sender.image_calls), 1)

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

    async def test_large_gpt_results_keep_original_bytes_for_qq_and_view_link(self):
        with Image.new("RGBA", (1200, 800), (0, 0, 255, 128)) as source:
            output = BytesIO()
            source.save(output, format="PNG", compress_level=0)
        image = ImageAsset(output.getvalue(), "image/png", 1200, 800)
        self.assertGreater(len(image.data), MAX_QQ_IMAGE_BYTES)
        for operation, upload_fails in product(("generate", "edit"), (False, True)):
            with self.subTest(operation=operation, upload_fails=upload_fails), \
                 tempfile.TemporaryDirectory() as directory:
                store = TemporaryMediaStore(Path(directory), "https://public.example", 60)
                await store.initialize()
                presenter, sender = self.make_presenter(media_store=store)
                if upload_fails:
                    sender.image_error = RuntimeError("QQ upload failed")
                with patch("aiqq.interfaces.qq.reply.prepare_qq_image") as prepare:
                    next_sequence = await presenter.send(
                        ConversationResult(
                            "ok", "answer", "answer", images=(image,),
                            image_provenance={"provider": "gpt", "operation": operation},
                        ),
                        group_id="group", source_message_id="source",
                        member_openid="member", first_msg_seq=2,
                    )
                prepare.assert_not_called()
                self.assertEqual(next_sequence, 4)
                self.assertEqual(len(sender.image_calls), 1)
                call = sender.image_calls[0]
                self.assertEqual(call["mime_type"], "image/png")
                file_name = Path(urlsplit(call["public_url"]).path).name
                self.assertTrue(file_name.endswith(".png"))
                stored_path = await store.resolve(file_name)
                self.assertEqual(stored_path.read_bytes(), image.data)
                metadata = call["image_metadata"]
                self.assertFalse(metadata["compressed"])
                self.assertEqual(metadata["source"]["sha256"], metadata["delivery"]["sha256"])
                self.assertEqual(metadata["source"]["size"], len(image.data))
                if upload_fails:
                    keyboard = str(sender.markdown_calls[-1]["keyboard"])
                    self.assertIn("view_image", keyboard)
                    self.assertIn(call["public_url"], keyboard)
                else:
                    self.assertEqual(sender.delivery_order, ["markdown", "image"])

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

    async def test_invalid_oversized_image_prevents_storage_upload_and_link(self):
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
        self.assertEqual(len(sender.markdown_calls), 1)
        self.assertNotIn("view_image", str(sender.markdown_calls[0]["keyboard"]))

    async def test_encode_or_resize_failure_keeps_original_viewable_until_expiry(self):
        with Image.new("RGB", (1200, 800), "blue") as source:
            output = BytesIO()
            source.save(output, format="PNG", compress_level=0)
        original = output.getvalue()
        self.assertGreater(len(original), MAX_QQ_IMAGE_BYTES)
        # The fallback derives MIME/dimensions from the original bytes.
        image = ImageAsset(original, "image/jpeg", 1, 1)
        for stage in ("encode", "resize"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                store = TemporaryMediaStore(Path(directory), "https://public.example", 60)
                await store.initialize()
                presenter, sender = self.make_presenter(media_store=store)
                operation = "save" if stage == "encode" else "resize"
                with patch(f"PIL.Image.Image.{operation}", side_effect=OSError("private error")), \
                     patch("aiqq.services.images.qq_upload.MAX_QQ_IMAGE_BYTES", 1):
                    with self.assertLogs("aiqq.interfaces.qq.reply", level="WARNING") as logs:
                        next_sequence = await presenter.send(
                            ConversationResult("ok", "answer", "answer", images=(image,)),
                            group_id="group", source_message_id="source",
                            member_openid="member", first_msg_seq=2,
                        )
                self.assertNotIn("private error", " ".join(logs.output))
                self.assertEqual(next_sequence, 4)
                self.assertEqual(sender.image_calls, [])
                self.assertEqual(sender.text_calls, [])
                notice = sender.markdown_calls[-1]
                self.assertEqual(notice["msg_seq"], 3)
                button = notice["keyboard"]["content"]["rows"][-1]["buttons"][0]
                public_url = button["action"]["data"]
                self.assertTrue(public_url.endswith(".png"))
                self.assertIn(IMAGE_VIEW_NOTICE_TEXT, notice["content"])
                self.assertEqual(len(list(store.directory.iterdir())), 1)
                path = await store.resolve(public_url.rsplit("/", 1)[-1])
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

                application = web.Application()
                register_media_routes(application, MediaHandler(store))
                async with TestClient(TestServer(application)) as client:
                    response = await client.get(urlsplit(public_url).path)
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.content_type, "image/png")
                    self.assertEqual(await response.read(), original)
                    self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                    old = time.time() - 120
                    os.utime(path, (old, old))
                    expired = await client.get(urlsplit(public_url).path)
                    self.assertEqual(expired.status, 404)
                self.assertFalse(path.exists())

    async def test_storage_failure_never_offers_broken_image_link_or_uploads(self):
        with Image.new("RGB", (16, 8), "blue") as source:
            output = BytesIO()
            source.save(output, format="PNG")
        image = ImageAsset(output.getvalue(), "image/png", 16, 8)
        for preparation_failed in (False, True):
            with self.subTest(preparation_failed=preparation_failed):
                store = SimpleNamespace(save=AsyncMock(side_effect=OSError("private path")))
                presenter, sender = self.make_presenter(media_store=store)
                with patch("aiqq.interfaces.qq.reply.prepare_qq_image",
                           side_effect=OSError("encoding failed") if preparation_failed else None,
                           return_value=image):
                    with self.assertLogs("aiqq.interfaces.qq.reply", level="WARNING") as logs:
                        self.assertEqual(await presenter.send(
                            ConversationResult("ok", "answer", "answer", images=(image,)),
                            group_id="group", source_message_id="source",
                            member_openid="member", first_msg_seq=2,
                        ), 4)
                self.assertNotIn("private path", " ".join(logs.output))
                self.assertEqual(store.save.await_count, 1)
                self.assertEqual(sender.image_calls, [])
                self.assertEqual(len(sender.markdown_calls), 1)
                self.assertNotIn("view_image", str(sender.markdown_calls[0]["keyboard"]))
                self.assertEqual(sender.text_calls[0]["content"], IMAGE_DELIVERY_FAILED_TEXT)

    async def test_upload_failure_link_opens_actual_prepared_jpeg(self):
        with Image.new("RGB", (1200, 800), "blue") as source:
            output = BytesIO()
            source.save(output, format="PNG", compress_level=0)
        image = ImageAsset(output.getvalue(), "image/png", 1200, 800)
        with tempfile.TemporaryDirectory() as directory:
            store = TemporaryMediaStore(Path(directory), "https://public.example", 60)
            await store.initialize()
            http = ImageRejectionHttp("upload")
            repository = SimpleNamespace(
                add_bot_message=AsyncMock(return_value=True),
                record_delivery_attempt=AsyncMock(return_value=1),
            )
            sender = QQMessageSender(BotAPI(http), repository, bot_username="AiQQ")
            presenter, _sender = self.make_presenter(sender=sender, media_store=store)
            next_sequence = await presenter.send(
                ConversationResult("ok", "answer", "answer", images=(image,)),
                group_id="group", source_message_id="source",
                member_openid="member", first_msg_seq=2,
            )
            self.assertEqual(next_sequence, 4)
            self.assertEqual(len(http.uploads), 1)
            self.assertEqual(http.image_sends, 0)
            notice = http.payloads[-1]
            button = notice["keyboard"]["content"]["rows"][-1]["buttons"][0]
            public_url = button["action"]["data"]
            self.assertEqual(public_url, http.uploads[0]["url"])
            self.assertTrue(public_url.endswith(".jpg"))
            application = web.Application()
            register_media_routes(application, MediaHandler(store))
            async with TestClient(TestServer(application)) as client:
                response = await client.get(urlsplit(public_url).path)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.content_type, "image/jpeg")
                data = await response.read()
                self.assertNotEqual(data, image.data)
                self.assertLessEqual(len(data), MAX_QQ_IMAGE_BYTES)
                with Image.open(BytesIO(data)) as decoded:
                    decoded.load()
                    self.assertEqual(decoded.format, "JPEG")
                    self.assertEqual(decoded.size, (1200, 800))

    async def test_failed_image_link_notice_stops_without_retry(self):
        presenter, sender = self.make_presenter()
        sender.image_error = RuntimeError("upload failed")
        for error in (RuntimeError("notice failed"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                sender.markdown_calls.clear()
                sender.image_calls.clear()
                initial_send = sender.send_markdown_reply

                async def fail_notice(**kwargs):
                    await initial_send(**kwargs)
                    if len(sender.markdown_calls) == 2:
                        raise error

                with patch.object(sender, "send_markdown_reply", fail_notice):
                    with self.assertRaises(type(error)):
                        await presenter.send(
                            ConversationResult("ok", "answer", "answer", images=(
                                ImageAsset(b"image", "image/jpeg", 1, 1),
                            )),
                            group_id="group", source_message_id="source",
                            member_openid="member", first_msg_seq=2,
                        )
                self.assertEqual(len(sender.image_calls), 1)
                self.assertEqual(len(sender.markdown_calls), 2)
                self.assertEqual(sender.text_calls, [])

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
