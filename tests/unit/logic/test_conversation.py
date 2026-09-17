import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiqq.exceptions import (
    ImageGenerationUnavailable,
    PromptAuditUnavailable,
    ReferenceImageUnavailable,
)
from aiqq.logic.conversation import (
    IMAGE_UNAVAILABLE_TEXT,
    OPTIONAL_IMAGE_FAILED_TEXT,
    PROMPT_AUDIT_UNAVAILABLE_TEXT,
    ConversationWorkflow,
)
from aiqq.logic.models import (
    ChatAgentOutput,
    ImageAction,
    ImagePromptAuditResult,
    ImageUsageDecision,
    PromptAuditResult,
)
from aiqq.logic.image_quota import ImageQuotaManager
from aiqq.services.images.reference import GroupReferenceImageLoader
from aiqq.services.images.web import WebImageService


class FakeAuditor:
    def __init__(self, result=None, error=None):
        self.result = result or PromptAuditResult(True, "safe", "")
        self.error = error

    async def audit(self, user_input):
        if self.error:
            raise self.error
        return self.result


class FakeHistory:
    async def get_current_for_reference(self, group_id, message_id):
        return None

    async def has_before(self, group_id, record_id):
        return False

    def __init__(self, messages=(), error=None):
        self.messages = messages
        self.error = error
        self.calls = []

    async def list_for_reference(self, group_id, *, before_message_id, limit):
        self.calls.append((group_id, before_message_id, limit))
        if self.error:
            raise self.error
        return self.messages


class FakeChat:
    def __init__(self, output=None):
        self.output = output or ChatAgentOutput("full answer", "结论喵。")
        self.requests = []

    async def run(self, request, *, on_progress=None):
        self.requests.append(request)
        return self.output


class FailingImageService:
    def __init__(self):
        self.calls = []

    async def generate(self, action, *, reference_image=None):
        self.calls.append((action, reference_image))
        raise RuntimeError("vendor secret must not escape")


class FakeImageAuditor:
    def __init__(self, *, safe=True, effective=True):
        self.result = ImagePromptAuditResult(
            safe=safe,
            effective=effective,
            category="safe" if safe else "adult_content",
            reason="accepted" if safe and effective else "rejected",
            suggested_prompt="" if safe and effective else "safe, sfw, landscape",
            orientation="square",
        )
        self.calls = []

    async def audit(self, prompt, *, require_english=False):
        self.calls.append((prompt, require_english))
        return self.result


class FailingWebImageService:
    async def download(self, url):
        raise RuntimeError("download failure")


class FakeImageUsage:
    def __init__(self, decision=None):
        self.decision = decision or ImageUsageDecision(True)
        self.reserved = []
        self.recorded = []
        self.finished = []

    async def reserve_attempt(self, key):
        self.reserved.append(key)
        return self.decision

    async def record_success(self, key):
        self.recorded.append(key)

    async def finish_attempt(self, key):
        self.finished.append(key)


class ConversationWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_audit_failure_stops_before_history_and_chat(self):
        history = FakeHistory()
        chat = FakeChat()
        workflow = ConversationWorkflow(
            prompt_auditor=FakeAuditor(error=PromptAuditUnavailable()),
            history_repository=history,
            conversation_agent=chat,
            image_usage=FakeImageUsage(),
        )

        result = await workflow.run(
            group_id="secret-group",
            before_message_id="m1",
            conversation_key="key",
            user_input="question",
            request_id="request-1",
        )

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.full_text, PROMPT_AUDIT_UNAVAILABLE_TEXT)
        self.assertEqual(history.calls, [])
        self.assertEqual(chat.requests, [])

    async def test_rejection_stops_before_history(self):
        history = FakeHistory()
        workflow = ConversationWorkflow(
            prompt_auditor=FakeAuditor(
                PromptAuditResult(False, "too_many_questions", "too many")
            ),
            history_repository=history,
            conversation_agent=FakeChat(),
            image_usage=FakeImageUsage(),
        )
        result = await workflow.run(
            group_id="g",
            before_message_id="m",
            conversation_key="key",
            user_input="two questions",
        )
        self.assertEqual(result.status, "rejected")
        self.assertEqual(history.calls, [])

    async def test_history_failure_continues_with_explicit_unavailable_reference(self):
        chat = FakeChat()
        workflow = ConversationWorkflow(
            prompt_auditor=FakeAuditor(),
            history_repository=FakeHistory(error=RuntimeError("database down")),
            conversation_agent=chat,
            image_usage=FakeImageUsage(),
        )
        with self.assertLogs("aiqq.logic.conversation", level="WARNING") as logs:
            result = await workflow.run(
                group_id="private-group-id",
                before_message_id="m",
                conversation_key="key",
                user_input="independent question",
                request_id="request-3",
            )

        self.assertEqual(result.status, "ok")
        self.assertFalse(chat.requests[0].reference_material_available)
        self.assertEqual(chat.requests[0].group_history, ())
        self.assertNotIn("private-group-id", "\n".join(logs.output))

    async def test_required_image_failure_is_explicitly_unavailable(self):
        chat = FakeChat(
            ChatAgentOutput(
                "正在生成图片。",
                "正在生成图片喵。",
                image_action=ImageAction("generate", "safe cat"),
            )
        )
        workflow = ConversationWorkflow(
            prompt_auditor=FakeAuditor(),
            history_repository=FakeHistory(),
            conversation_agent=chat,
            image_usage=FakeImageUsage(),
            image_service=FailingImageService(),
            image_prompt_auditor=FakeImageAuditor(),
        )
        result = await workflow.run(
            group_id="g",
            before_message_id="m",
            conversation_key="key",
            user_input="draw a cat",
        )
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.full_text, IMAGE_UNAVAILABLE_TEXT)

    async def test_rejected_model_image_prompt_never_reaches_image_api(self):
        image_service = FailingImageService()
        image_auditor = FakeImageAuditor(safe=False)
        usage = FakeImageUsage()
        workflow = ConversationWorkflow(
            prompt_auditor=FakeAuditor(),
            history_repository=FakeHistory(),
            conversation_agent=FakeChat(
                ChatAgentOutput(
                    "正在生成图片。",
                    "生成图片喵。",
                    image_action=ImageAction("generate", "unsafe model prompt"),
                )
            ),
            image_usage=usage,
            image_service=image_service,
            image_prompt_auditor=image_auditor,
        )

        result = await workflow.run(
            group_id="g",
            before_message_id="m",
            conversation_key="key",
            user_input="draw",
        )

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(image_auditor.calls, [("unsafe model prompt", False)])
        self.assertEqual(image_service.calls, [])
        self.assertEqual(usage.reserved, [])

    async def test_image_failures_share_classification_and_release_quota(self):
        for mode in ("generate", "edit"):
            for error in (
                ImageGenerationUnavailable(
                    "private provider message sk-secret",
                    kind="not_found", attempt_id="attempt-456", status_code=404,
                ),
                asyncio.CancelledError(),
            ):
                with self.subTest(mode=mode, error=type(error).__name__):
                    repository = AsyncMock()
                    repository.success_count.return_value = 0
                    quota = ImageQuotaManager(repository, daily_limit=100)
                    images = AsyncMock()
                    images.generate.side_effect = error
                    references = AsyncMock()
                    workflow = ConversationWorkflow(
                        prompt_auditor=FakeAuditor(),
                        history_repository=FakeHistory(),
                        conversation_agent=FakeChat(ChatAgentOutput(
                            "正在生成图片。", "生成图片喵。",
                            image_action=ImageAction(
                                mode, "safe cat",
                                source_record_id=7 if mode == "edit" else None,
                            ),
                        )),
                        image_usage=quota,
                        image_service=images,
                        image_prompt_auditor=FakeImageAuditor(),
                        reference_image_loader=references,
                    )
                    kwargs = dict(
                        group_id="group", before_message_id="message",
                        conversation_key="key", user_input="draw a cat",
                        request_id="conversation-123",
                    )
                    if isinstance(error, asyncio.CancelledError):
                        with self.assertRaises(asyncio.CancelledError):
                            await workflow.run(**kwargs)
                    else:
                        with self.assertLogs("aiqq.logic.conversation") as logs:
                            result = await workflow.run(**kwargs)
                        self.assertEqual(result.error_code, "image_service_not_found")
                        self.assertEqual(result.full_text, "图片服务请求失败，暂时无法生成。")
                        self.assertEqual(result.images, ())
                        self.assertIn("attempt_id=attempt-456", "\n".join(logs.output))
                        self.assertIn("request_id=conversation-123", "\n".join(logs.output))
                        self.assertNotIn("sk-secret", result.full_text + "\n".join(logs.output))
                    if mode == "edit":
                        references.load.assert_awaited_once_with("group", 7)
                    else:
                        references.load.assert_not_awaited()
                    repository.increment_success.assert_not_awaited()
                    decision = await quota.reserve_attempt("next-member")
                    self.assertTrue(decision.allowed)
                    await quota.finish_attempt("next-member")

    async def test_optional_web_image_failure_keeps_text_and_explains(self):
        chat = FakeChat(
            ChatAgentOutput(
                "这是主体回答。",
                "主体结论喵。",
                selected_web_image_url="https://images.example.com/item.jpg",
            )
        )
        workflow = ConversationWorkflow(
            prompt_auditor=FakeAuditor(),
            history_repository=FakeHistory(),
            conversation_agent=chat,
            image_usage=FakeImageUsage(),
            web_image_service=FailingWebImageService(),
        )
        result = await workflow.run(
            group_id="g",
            before_message_id="m",
            conversation_key="key",
            user_input="show item",
        )
        self.assertEqual(result.status, "ok")
        self.assertIn(OPTIONAL_IMAGE_FAILED_TEXT, result.full_text)
        self.assertEqual(result.images, ())

    def make_edit_workflow(self, loader, usage, images):
        return ConversationWorkflow(
            prompt_auditor=FakeAuditor(),
            history_repository=FakeHistory(),
            conversation_agent=FakeChat(ChatAgentOutput(
                "正在改图。", "正在改图喵。",
                image_action=ImageAction("edit", "safe cat", source_record_id=609),
            )),
            image_usage=usage,
            image_service=images,
            image_prompt_auditor=FakeImageAuditor(),
            reference_image_loader=loader,
        )

    async def run_edit(self, workflow):
        return await workflow.run(
            group_id="group", before_message_id="message", conversation_key="key",
            user_input="edit the original image", request_id="reference-regression",
        )

    async def test_actual_download_404_and_410_reach_visible_expiry_without_generation(self):
        for status in (404, 410):
            with self.subTest(status=status):
                downloader = WebImageService()
                session = MagicMock()
                session.get.return_value.__aenter__.return_value.status = status
                references = AsyncMock()
                references.list_image_urls.return_value = ("https://images.example/original?token=secret",)
                loader = GroupReferenceImageLoader(repository=references, downloader=downloader)
                usage_repository = AsyncMock()
                usage_repository.success_count.return_value = 0
                quota = ImageQuotaManager(usage_repository, daily_limit=100)
                images = AsyncMock()
                workflow = self.make_edit_workflow(loader, quota, images)
                with patch.object(downloader, "_get_session", return_value=session), \
                     patch.object(quota, "reserve_attempt", wraps=quota.reserve_attempt) as reserve, \
                     self.assertLogs("aiqq", level="INFO") as logs:
                    result = await self.run_edit(workflow)
                    reserve.assert_not_awaited()
                self.assertEqual(result.status, "unavailable")
                self.assertEqual(result.error_code, "reference_image_expired")
                self.assertEqual(result.full_text, "引用的图片已过期或已失效，请重新发送原图后再试。")
                self.assertEqual(result.summary, result.full_text)
                self.assertEqual(result.images, ())
                session.get.assert_called_once()
                references.list_image_urls.assert_awaited_once_with("group", 609)
                images.generate.assert_not_awaited()
                usage_repository.success_count.assert_not_awaited()
                usage_repository.increment_success.assert_not_awaited()
                log_text = "\n".join(logs.output)
                self.assertIn(f"status={status}", log_text)
                self.assertIn("request_id=reference-regression", log_text)
                self.assertNotIn("token", log_text)
                self.assertNotIn("secret", log_text)
                self.assertNotIn("https://", log_text)
                self.assertTrue((await quota.reserve_attempt("next-member")).allowed)
                await quota.finish_attempt("next-member")

    async def test_reference_failures_show_reason_and_action_in_summary_and_full_text(self):
        cases = (
            ("missing", "未找到可用的原图，请重新发送图片后再试。"),
            ("timeout", "读取原图超时，请稍后重试，或重新发送图片。"),
            ("access_denied", "无法访问原图，请重新发送图片后再试。"),
            ("invalid_image", "原图无法读取，请重新发送有效的图片后再试。"),
            ("too_large", "原图过大，读取失败，请压缩后重新发送。"),
            ("download_failed", "原图下载失败，请稍后重试，或重新发送图片。"),
            ("unknown\nprivate-kind", "原图下载失败，请稍后重试，或重新发送图片。"),
        )
        for kind, text in cases:
            with self.subTest(kind=kind):
                loader = AsyncMock()
                loader.load.side_effect = ReferenceImageUnavailable(
                    "private detail https://private?token=secret", kind=kind,
                )
                usage = FakeImageUsage()
                images = AsyncMock()
                with self.assertLogs("aiqq.logic.conversation") as logs:
                    result = await self.run_edit(self.make_edit_workflow(loader, usage, images))
                expected_kind = "download_failed" if kind.startswith("unknown") else kind
                self.assertEqual(result.error_code, f"reference_image_{expected_kind}")
                self.assertEqual(result.full_text, text)
                self.assertEqual(result.summary, text)
                self.assertEqual(result.status, "unavailable")
                self.assertEqual(result.images, ())
                self.assertLessEqual(len(result.summary), 50)
                self.assertNotIn("private", "\n".join(logs.output))
                images.generate.assert_not_awaited()
                self.assertEqual(usage.reserved, [])
                self.assertEqual(usage.recorded, [])
                self.assertEqual(usage.finished, [])

    async def test_legacy_none_reference_is_missing_and_cancellation_propagates(self):
        for failure in (None, asyncio.CancelledError()):
            with self.subTest(failure=type(failure).__name__):
                loader = AsyncMock()
                if failure is None:
                    loader.load.return_value = None
                else:
                    loader.load.side_effect = failure
                usage = FakeImageUsage()
                images = AsyncMock()
                workflow = self.make_edit_workflow(loader, usage, images)
                if failure is None:
                    result = await self.run_edit(workflow)
                    self.assertEqual(result.error_code, "reference_image_missing")
                    self.assertEqual(result.summary, result.full_text)
                else:
                    with self.assertRaises(asyncio.CancelledError):
                        await self.run_edit(workflow)
                images.generate.assert_not_awaited()
                self.assertEqual(usage.reserved, [])
                self.assertEqual(usage.recorded, [])


if __name__ == "__main__":
    unittest.main()
