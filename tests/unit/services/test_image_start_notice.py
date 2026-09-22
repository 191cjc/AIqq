import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiqq.exceptions import ImageAuditUnavailable, ReferenceImageUnavailable
from aiqq.interfaces.qq.command_router import ExplicitCommandRouter
from aiqq.interfaces.qq.handlers import GroupMessageHandler, IncomingGroupMessage
from aiqq.interfaces.qq.progress import QQProgressReporter
from aiqq.interfaces.qq.reply import (
    ConversationReplySender,
    DEFERRED_NOTICE_TEXT,
    IMAGE_START_NOTICE_TEXT,
)
from aiqq.logic.conversation import ConversationWorkflow
from aiqq.logic.image_generation import GPTImageWorkflow, notify_image_start
from aiqq.logic.image_quota import ImageQuotaManager
from aiqq.logic.models import (
    BotSentGroupMessage,
    ChatAgentOutput,
    ConversationResult,
    ImageAction,
    ImageAsset,
    ImagePromptAuditResult,
    ImageUsageDecision,
    NovelAIPromptOptions,
    NovelAIPromptSession,
    NovelAIPromptWorkflowResult,
    PromptAuditResult,
)
from aiqq.logic.novelai import NovelAIImageWorkflow


class ImageRequestFixture:
    """Real workflows, router and presenter; only external operations are faked."""

    def __init__(self, provider):
        self.provider = provider
        self.events = []
        self.sends = []
        self.recalled = []
        self.notice_error = None
        self.provider_error = None
        self.progress_error = None
        self.audit_error = None
        self.audit_safe = True
        self.style_error = None
        self.quota_allowed = True
        self.reference_error = None
        self.progress_updates = 0
        self.chat_entered = asyncio.Event()
        self.release_chat = asyncio.Event()
        self.release_chat.set()
        self.image = ImageAsset(b"image", "image/jpeg", 1, 1)
        self.sender = SimpleNamespace(
            send_text_reply=self.send_text,
            send_markdown_reply=self.send_markdown,
            send_image_reply=self.send_image,
            recall_from_group=self.recall,
        )
        self.reply_store = SimpleNamespace(
            save=AsyncMock(return_value=SimpleNamespace(public_url="https://example/full")),
            create_pending=AsyncMock(return_value=SimpleNamespace(
                file_name="pending.md", public_url="https://example/pending"
            )),
            update=AsyncMock(return_value=True),
        )
        self.reply_sender = ConversationReplySender(
            sender=self.sender, reply_store=self.reply_store,
            media_store=SimpleNamespace(save=AsyncMock(return_value=SimpleNamespace(
                public_url="https://example/image"
            ))),
        )
        self.usage = SimpleNamespace(
            reserve_attempt=self.reserve, record_success=self.success,
            finish_attempt=self.finish,
        )
        self.image_auditor = SimpleNamespace(audit=self.audit)
        self.images = SimpleNamespace(generate=self.generate)
        self.styles = SimpleNamespace(load=self.style)
        self.chat_output = ChatAgentOutput(
            "图片已生成。", "图片已生成。",
            image_action=ImageAction(
                "edit" if provider == "edit" else "generate", "white cat",
                source_record_id=12 if provider == "edit" else None,
            ),
        )
        self.gpt = GPTImageWorkflow(
            prompt_auditor=self.image_auditor, image_service=self.images,
            image_usage=self.usage,
        )
        self.novelai = NovelAIImageWorkflow(
            prompt_auditor=self.image_auditor, image_service=self.images,
            image_usage=self.usage, styles=self.styles,
        )
        self.conversation = ConversationWorkflow(
            prompt_auditor=SimpleNamespace(audit=AsyncMock(
                return_value=PromptAuditResult(True, "safe", "")
            )),
            history_repository=SimpleNamespace(
                list_for_reference=AsyncMock(return_value=()),
                get_current_for_reference=AsyncMock(return_value=None),
            ),
            conversation_agent=SimpleNamespace(run=self.chat),
            image_usage=self.usage, image_service=self.images,
            image_prompt_auditor=self.image_auditor,
            reference_image_loader=SimpleNamespace(load=self.reference),
            web_image_service=SimpleNamespace(download=AsyncMock(return_value=self.image)),
        )
        prompt_result = NovelAIPromptWorkflowResult(
            ConversationResult("ok", "options", "options"),
            NovelAIPromptSession("token", NovelAIPromptOptions(("cat", "dog", "bird"))),
        )
        self.router = ExplicitCommandRouter(
            reply_sender=self.reply_sender,
            gpt_image_workflow=self.gpt, novelai_image_workflow=self.novelai,
            novelai_quick_image_workflow=SimpleNamespace(run=AsyncMock(
                return_value=ConversationResult("ok", "quick", "quick")
            )),
            novelai_prompt_workflow=SimpleNamespace(
                create=AsyncMock(return_value=prompt_result),
                revise=AsyncMock(return_value=prompt_result),
            ),
            novelai_style_workflow=SimpleNamespace(run=AsyncMock(
                return_value=ConversationResult("ok", "saved", "saved")
            )),
        )

    async def style(self, member):
        self.events.append("style")
        if self.style_error:
            raise self.style_error
        return "watercolor"

    async def audit(self, prompt, **kwargs):
        self.events.append("audit")
        if self.audit_error:
            raise self.audit_error
        return ImagePromptAuditResult(
            self.audit_safe, True, "safe" if self.audit_safe else "adult_content",
            "audit result", "" if self.audit_safe else "safe cat", "portrait"
        )

    async def reserve(self, key):
        self.events.append("reserve")
        return (
            ImageUsageDecision(True) if self.quota_allowed
            else ImageUsageDecision(False, "daily_limit", 3)
        )

    async def success(self, key):
        self.events.append("success")

    async def finish(self, key):
        self.events.append("finish")

    async def generate(self, *args, **kwargs):
        self.events.append("generate")
        if self.provider_error:
            raise self.provider_error
        return self.image

    async def reference(self, *args):
        self.events.append("reference")
        if self.reference_error:
            raise self.reference_error
        return self.image

    async def chat(self, request, *, on_progress=None):
        self.chat_entered.set()
        await self.release_chat.wait()
        for _ in range(self.progress_updates):
            await on_progress("正在检索。")
        return self.chat_output

    async def send_text(self, **kwargs):
        self.sends.append(("text", kwargs))
        if kwargs["content"] == IMAGE_START_NOTICE_TEXT:
            self.events.append("notice")
            if self.notice_error:
                raise self.notice_error
        else:
            self.events.append("progress" if kwargs.get("progress") else "fallback")
            if kwargs.get("progress") and self.progress_error:
                raise self.progress_error
        return BotSentGroupMessage(
            kwargs["group_id"], f"sent-{len(self.sends)}",
            datetime.now(timezone.utc), True,
        )

    async def send_markdown(self, **kwargs):
        self.sends.append(("markdown", kwargs))
        self.events.append("deferred" if DEFERRED_NOTICE_TEXT in kwargs["content"] else "final")

    async def send_image(self, **kwargs):
        self.sends.append(("image", kwargs))
        self.events.append("image")

    async def recall(self, target):
        self.recalled.append(target.message_id)
        return True

    def handler(self, *, timeout=10, workflow=None):
        return GroupMessageHandler(
            workflow=workflow or self.conversation, sender=self.sender,
            reply_sender=self.reply_sender, command_handler=self.router,
            defer_after_seconds=timeout,
        )

    def message(self, *, slash=True, prompt="white cat", content=None):
        if content is None:
            if self.provider in ("gpt", "novelai"):
                command = "GPT生图" if self.provider == "gpt" else "NovelAI生图"
                content = ("/" if slash else "") + command + " " + prompt
            else:
                content = "请生成图片" if self.provider == "generate" else "请修改这张图"
        return IncomingGroupMessage("group", "source", "member", content)

    def sequences(self):
        return [kwargs["msg_seq"] for _, kwargs in self.sends]


class ImageStartNoticeTests(unittest.IsolatedAsyncioTestCase):
    async def test_notice_after_admission_before_each_provider_and_final_delivery(self):
        for provider in ("gpt", "novelai", "generate", "edit"):
            for slash in (False, True):
                with self.subTest(provider=provider, slash=slash):
                    fixture = ImageRequestFixture(provider)
                    handler = fixture.handler()
                    message = fixture.message(slash=slash)
                    self.assertTrue(await handler.handle(message))
                    self.assertFalse(await handler.handle(message))
                    before = ["style"] if provider == "novelai" else []
                    before += ["audit"]
                    if provider == "edit":
                        before += ["reference"]
                    self.assertEqual(fixture.events, before + [
                        "reserve", "notice", "generate", "success", "finish", "final", "image"
                    ])
                    self.assertEqual(fixture.sequences(), [1, 2, 3])
                    kind, notice = fixture.sends[0]
                    self.assertEqual(kind, "text")
                    self.assertEqual(notice["content"], IMAGE_START_NOTICE_TEXT)
                    self.assertEqual(notice["group_id"], "group")
                    self.assertEqual(notice["source_message_id"], "source")
                    self.assertEqual(notice["fallback_member_openid"], "member")
                    self.assertTrue(notice["quote_source"])
                    self.assertFalse(notice.get("progress", False))
                    self.assertEqual(fixture.recalled, [])

    async def test_rejected_requests_never_announce_or_generate(self):
        for provider in ("gpt", "novelai", "generate", "edit"):
            for reason in ("audit", "audit_error", "quota"):
                with self.subTest(provider=provider, reason=reason):
                    fixture = ImageRequestFixture(provider)
                    if reason == "audit":
                        fixture.audit_safe = False
                    elif reason == "audit_error":
                        fixture.audit_error = ImageAuditUnavailable()
                    else:
                        fixture.quota_allowed = False
                    await fixture.handler().handle(fixture.message())
                    self.assertNotIn("notice", fixture.events)
                    self.assertNotIn("generate", fixture.events)
                    self.assertEqual(fixture.sequences(), [1])
        for provider in ("gpt", "novelai"):
            fixture = ImageRequestFixture(provider)
            await fixture.handler().handle(fixture.message(prompt=""))
            self.assertEqual(fixture.events, ["final"])
        fixture = ImageRequestFixture("edit")
        fixture.reference_error = ReferenceImageUnavailable(kind="expired")
        await fixture.handler().handle(fixture.message())
        self.assertEqual(fixture.events, ["audit", "reference", "final"])
        fixture = ImageRequestFixture("novelai")
        fixture.style_error = RuntimeError("private style")
        await fixture.handler().handle(fixture.message())
        self.assertEqual(fixture.events, ["style", "final"])

    async def test_text_web_search_and_prompt_or_style_commands_have_no_notice(self):
        for content in ("/NovelAI提示词 cat", "/NovelAI画风 watercolor", "/NovelAI画风"):
            fixture = ImageRequestFixture("novelai")
            await fixture.handler().handle(fixture.message(content=content))
            self.assertEqual(fixture.events, ["final"])
        for web_url in (None, "https://example/cat"):
            fixture = ImageRequestFixture("generate")
            fixture.chat_output = ChatAgentOutput(
                "文字回答", "文字回答", selected_web_image_url=web_url
            )
            await fixture.handler().handle(fixture.message())
            self.assertNotIn("notice", fixture.events)
            self.assertNotIn("generate", fixture.events)

    async def test_notice_failure_is_sanitized_and_consumes_sequence_without_retry(self):
        for provider in ("gpt", "novelai", "generate", "edit"):
            with self.subTest(provider=provider):
                fixture = ImageRequestFixture(provider)
                fixture.notice_error = RuntimeError("private url token member prompt")
                with self.assertLogs("aiqq.logic.image_generation", "WARNING") as logs:
                    await fixture.handler().handle(fixture.message())
                self.assertEqual([record.getMessage() for record in logs.records], [
                    "event=image_start_notice_failed error_type=RuntimeError"
                ])
                self.assertEqual(fixture.events.count("notice"), 1)
                self.assertEqual(fixture.events.count("generate"), 1)
                self.assertEqual(fixture.sequences(), [1, 2, 3])

    async def test_notice_cancellation_releases_real_quota_and_never_generates(self):
        for provider in ("gpt", "novelai", "generate", "edit"):
            with self.subTest(provider=provider):
                fixture = ImageRequestFixture(provider)
                repository = SimpleNamespace(
                    success_count=AsyncMock(return_value=0), increment_success=AsyncMock()
                )
                quota = ImageQuotaManager(repository, daily_limit=3)
                for workflow in (fixture.gpt, fixture.novelai, fixture.conversation):
                    workflow._image_usage = quota
                fixture.notice_error = asyncio.CancelledError()
                with self.assertRaises(asyncio.CancelledError):
                    await fixture.handler().handle(fixture.message())
                self.assertNotIn("generate", fixture.events)
                self.assertNotIn("final", fixture.events)
                repository.increment_success.assert_not_awaited()
                decision = await quota.reserve_attempt("other-member")
                self.assertTrue(decision.allowed)
                await quota.finish_attempt("other-member")

    async def test_start_notice_survives_progress_cap_and_is_never_recalled(self):
        fixture = ImageRequestFixture("generate")
        fixture.progress_updates = 3
        await fixture.handler().handle(fixture.message())
        self.assertEqual(fixture.events.count("progress"), 2)
        self.assertEqual(fixture.events.count("notice"), 1)
        self.assertEqual(fixture.sequences(), [1, 2, 3, 4, 5])
        self.assertEqual(fixture.recalled, ["sent-1", "sent-2"])

    async def test_one_shot_callback_even_if_caller_invokes_twice(self):
        for provider in ("gpt", "novelai", "generate"):
            for fails in (False, True):
                with self.subTest(provider=provider, fails=fails):
                    fixture = ImageRequestFixture(provider)
                    if fails:
                        fixture.notice_error = RuntimeError("private failure")

                    async def duplicate(*args, on_image_start, **kwargs):
                        await notify_image_start(on_image_start)
                        await notify_image_start(on_image_start)
                        return ConversationResult("ok", "done", "done")

                    if provider == "gpt":
                        fixture.gpt.run = duplicate
                    elif provider == "novelai":
                        fixture.novelai.run = duplicate
                    await fixture.handler(workflow=SimpleNamespace(run=duplicate)).handle(
                        fixture.message()
                    )
                    self.assertEqual(fixture.events, ["notice", "final"])
                    self.assertEqual(fixture.sequences(), [1, 2])

    async def test_late_start_uses_live_sequence_after_deferred_notice(self):
        for page_fails in (False, True):
            for notice_fails in (False, True):
                with self.subTest(page_fails=page_fails, notice_fails=notice_fails):
                    fixture = ImageRequestFixture("edit")
                    fixture.release_chat.clear()
                    if page_fails:
                        fixture.reply_store.create_pending.side_effect = RuntimeError("disk")
                    if notice_fails:
                        fixture.notice_error = RuntimeError("QQ")
                    handler = fixture.handler(timeout=0.001)
                    self.assertTrue(await handler.handle(fixture.message()))
                    fixture.release_chat.set()
                    await asyncio.wait_for(asyncio.gather(*handler._deferred_tasks), 1)
                    self.assertEqual(fixture.events[-6:], [
                        "notice", "generate", "success", "finish", "final", "image"
                    ])
                    expected = [2, 3, 4, 5] if page_fails else [1, 2, 3, 4]
                    self.assertEqual(fixture.sequences(), expected)

    async def test_pending_page_setup_serializes_late_image_notice(self):
        for page_fails in (False, True):
            with self.subTest(page_fails=page_fails):
                fixture = ImageRequestFixture("generate")
                fixture.release_chat.clear()
                page_entered = asyncio.Event()
                page_release = asyncio.Event()

                async def pending(content):
                    page_entered.set()
                    await page_release.wait()
                    if page_fails:
                        raise RuntimeError("disk")
                    return SimpleNamespace(file_name="pending", public_url="https://example/page")

                fixture.reply_store.create_pending.side_effect = pending
                handler = fixture.handler(timeout=0.001)
                task = asyncio.create_task(handler.handle(fixture.message()))
                await asyncio.wait_for(page_entered.wait(), 1)
                fixture.release_chat.set()
                # Wait until the workflow is admitted and waiting for the notice lock.
                for _ in range(20):
                    if "reserve" in fixture.events:
                        break
                    await asyncio.sleep(0)
                self.assertIn("reserve", fixture.events)
                self.assertNotIn("notice", fixture.events)
                self.assertNotIn("generate", fixture.events)
                page_release.set()
                self.assertTrue(await asyncio.wait_for(task, 1))
                await asyncio.wait_for(asyncio.gather(*handler._deferred_tasks), 1)
                expected = [2, 3, 4, 5] if page_fails else [1, 2, 3, 4]
                self.assertEqual(fixture.sequences(), expected)
                self.assertLess(
                    fixture.events.index("fallback" if page_fails else "deferred"),
                    fixture.events.index("notice"),
                )

    async def test_failed_progress_send_also_consumes_sequence(self):
        fixture = ImageRequestFixture("generate")
        fixture.progress_error = TimeoutError("ambiguous QQ outcome")
        reporter = QQProgressReporter(fixture.sender, group_id="group", source_message_id="source")
        with self.assertRaises(TimeoutError):
            await reporter.publish("progress")
        await reporter.finish()
        await fixture.reply_sender.send_image_start(
            group_id="group", source_message_id="source", member_openid="member",
            msg_seq=reporter.reserve_msg_seq(),
        )
        self.assertEqual(fixture.sequences(), [1, 2])
        self.assertEqual(reporter.next_msg_seq, 3)

    async def test_cancellation_during_page_setup_cancels_waiting_image_workflow(self):
        fixture = ImageRequestFixture("generate")
        fixture.release_chat.clear()
        page_entered = asyncio.Event()

        async def pending(content):
            page_entered.set()
            await asyncio.Event().wait()

        fixture.reply_store.create_pending.side_effect = pending
        handler = fixture.handler(timeout=0.001)
        task = asyncio.create_task(handler.handle(fixture.message()))
        await asyncio.wait_for(page_entered.wait(), 1)
        fixture.release_chat.set()
        for _ in range(20):
            if "reserve" in fixture.events:
                break
            await asyncio.sleep(0)
        self.assertIn("reserve", fixture.events)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(fixture.events, ["audit", "reserve", "finish"])
        self.assertEqual(fixture.sends, [])
        self.assertEqual(handler._deferred_tasks, set())


if __name__ == "__main__":
    unittest.main()
