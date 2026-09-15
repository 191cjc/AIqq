import asyncio
import unittest
from unittest.mock import AsyncMock

from aiqq.exceptions import ImageGenerationUnavailable, PromptAuditUnavailable
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


class FakeAuditor:
    def __init__(self, result=None, error=None):
        self.result = result or PromptAuditResult(True, "safe", "")
        self.error = error

    async def audit(self, user_input):
        if self.error:
            raise self.error
        return self.result


class FakeHistory:
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


if __name__ == "__main__":
    unittest.main()
