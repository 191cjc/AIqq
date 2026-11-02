"""Requested web images survive model parsing, downloads and QQ presentation."""

import asyncio
import json
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

from PIL import Image

from aiqq.interfaces.qq.reply import ConversationReplySender
from aiqq.logic.conversation import (
    OPTIONAL_IMAGE_FAILED_TEXT,
    PARTIAL_IMAGE_FAILED_TEXT,
    ConversationWorkflow,
)
from aiqq.logic.models import (
    ChatAgentOutput,
    ConversationResult,
    ImageAsset,
    PromptAuditResult,
)
from aiqq.logic.ports import AgentTurnResult
from aiqq.services.agents.chat import ChatAgent
from aiqq.services.images.web import WebImageError


class WebImageReplyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.urls = [f"https://images.example.com/{index}.png" for index in range(5)]
        self.images = []
        for index in range(5):
            data = BytesIO()
            with Image.new("RGB", (8 + index, 8), (index * 40, 20, 30)) as image:
                image.save(data, format="PNG")
            self.images.append(ImageAsset(data.getvalue(), "image/png", 8 + index, 8))

    def workflow(self, urls, download):
        self.backend = AsyncMock()
        self.backend.run.return_value = AgentTurnResult(json.dumps({
            "full_text": "找到图片。", "summary": "找到图片。",
            "image_action": None, "selected_web_image_urls": urls, "sources": [],
        }))
        history = AsyncMock()
        history.list_for_reference.return_value = ()
        history.get_current_for_reference.return_value = None
        audit = AsyncMock()
        audit.audit.return_value = PromptAuditResult(True, "safe", "")
        self.usage = AsyncMock()
        self.generation = AsyncMock()
        self.downloader = AsyncMock()
        self.downloader.download.side_effect = download
        self.notice = AsyncMock()
        return ConversationWorkflow(
            prompt_auditor=audit,
            history_repository=history,
            conversation_agent=ChatAgent(self.backend, system_prompt="system"),
            image_usage=self.usage,
            image_service=self.generation,
            web_image_service=self.downloader,
        )

    async def run_workflow(self, workflow, user_input="请找5张猫的图片"):
        return await asyncio.wait_for(workflow.run(
            group_id="group", before_message_id="message", conversation_key="key",
            user_input=user_input, on_image_start=self.notice,
        ), timeout=5)

    def assert_no_generation(self):
        self.usage.reserve_attempt.assert_not_awaited()
        self.usage.record_success.assert_not_awaited()
        self.usage.finish_attempt.assert_not_awaited()
        self.generation.generate.assert_not_awaited()
        self.notice.assert_not_awaited()

    async def send_result(self, result):
        sender = AsyncMock()
        media = AsyncMock()
        stored = []

        async def save(image):
            stored.append(image)
            return SimpleNamespace(public_url=f"https://public.example/{len(stored)}.png")

        media.save.side_effect = save
        presenter = ConversationReplySender(
            sender=sender, reply_store=AsyncMock(), media_store=media,
        )
        next_sequence = await presenter.send(
            result, group_id="group", source_message_id="message",
            member_openid="member", first_msg_seq=2,
        )
        return sender, stored, next_sequence

    async def test_requested_count_reaches_qq_in_selection_order(self):
        for requested, count in ((0, 0), (1, 1), (3, 3), (5, 5), (10, 5)):
            with self.subTest(requested=requested):
                urls = self.urls[:count]
                release = [asyncio.Event() for _ in urls]
                completed = []

                async def download(url):
                    index = urls.index(url)
                    if index < count - 1:
                        await release[index].wait()
                    completed.append(url)
                    if index:
                        release[index - 1].set()
                    return self.images[index]

                workflow = self.workflow(urls, download)
                result = await self.run_workflow(workflow, f"请找{requested}张猫的图片")
                self.assertEqual(result.status, "ok")
                self.assertEqual(result.images, tuple(self.images[:count]))
                self.assertEqual(completed, list(reversed(urls)))
                self.assertEqual(self.downloader.download.await_args_list, [call(url) for url in urls])
                self.assertEqual(result.image_provenance.get("source_urls", []), urls)
                sender, stored, next_sequence = await self.send_result(result)
                self.assertEqual(stored, self.images[:count])
                self.assertEqual(sender.send_markdown_reply.await_count, 1)
                self.assertEqual(sender.send_markdown_reply.await_args.kwargs["msg_seq"], 2)
                self.assertEqual(sender.send_image_reply.await_count, count)
                self.assertEqual([
                    item.kwargs["msg_seq"] for item in sender.send_image_reply.await_args_list
                ], list(range(3, 3 + count)))
                self.assertEqual([
                    item.kwargs["public_url"] for item in sender.send_image_reply.await_args_list
                ], [f"https://public.example/{index + 1}.png" for index in range(count)])
                self.assertEqual(next_sequence, 3 + count)
                self.assert_no_generation()

    async def test_duplicate_selections_are_downloaded_and_sent_once(self):
        urls = [self.urls[2], self.urls[0], self.urls[2]]

        async def download(url):
            return self.images[self.urls.index(url)]

        result = await self.run_workflow(self.workflow(urls, download))
        self.assertEqual(result.images, (self.images[2], self.images[0]))
        self.assertEqual(self.downloader.download.await_args_list, [call(self.urls[2]), call(self.urls[0])])
        sender, stored, _ = await self.send_result(result)
        self.assertEqual(stored, [self.images[2], self.images[0]])
        self.assertEqual(sender.send_image_reply.await_count, 2)
        self.assert_no_generation()

    async def test_download_failures_preserve_successes_and_original_answer(self):
        for failed in ({0}, {2, 4}, set(range(5))):
            with self.subTest(failed=failed):
                async def download(url):
                    index = self.urls.index(url)
                    if index in failed:
                        raise WebImageError("private-url private-token private-body")
                    return self.images[index]

                workflow = self.workflow(self.urls, download)
                with self.assertLogs("aiqq.logic.conversation", "WARNING") as logs:
                    result = await self.run_workflow(workflow)
                expected_images = [image for index, image in enumerate(self.images) if index not in failed]
                self.assertEqual(result.images, tuple(expected_images))
                self.assertEqual(result.image_provenance.get("source_urls", []), [
                    url for index, url in enumerate(self.urls) if index not in failed
                ])
                self.assertEqual(result.status, "ok")
                self.assertTrue(result.full_text.startswith("找到图片。"))
                self.assertIn(
                    PARTIAL_IMAGE_FAILED_TEXT if expected_images else OPTIONAL_IMAGE_FAILED_TEXT,
                    result.full_text,
                )
                self.assertNotIn("private-", result.full_text + "\n".join(logs.output))
                sender, stored, _ = await self.send_result(result)
                self.assertEqual(stored, expected_images)
                self.assertEqual(sender.send_image_reply.await_count, len(expected_images))
                self.assert_no_generation()

    async def test_cancellation_cancels_all_pending_downloads(self):
        entered = asyncio.Event()
        pending, cancelled = [], []

        async def download(url):
            pending.append(url)
            if len(pending) == 5:
                entered.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.append(url)

        task = asyncio.create_task(self.run_workflow(self.workflow(self.urls, download)))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertCountEqual(cancelled, self.urls)
            self.assert_no_generation()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def test_domain_limits_prevent_more_than_five_web_images(self):
        with self.assertRaises(ValueError):
            ChatAgentOutput("answer", "answer", selected_web_image_urls=tuple(self.urls + [self.urls[0]]))
        with self.assertRaises(ValueError):
            ConversationResult("ok", "answer", "answer", images=tuple(self.images + [self.images[0]]))
        with self.assertRaises(ValueError):
            ChatAgentOutput("answer", "answer", generated_images=tuple(self.images[:2]))
