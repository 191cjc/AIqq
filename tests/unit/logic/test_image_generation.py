import asyncio
import unittest
from unittest.mock import AsyncMock

from aiqq.exceptions import ImageAuditUnavailable, ImageGenerationUnavailable
from aiqq.logic.image_generation import GPTImageWorkflow
from aiqq.logic.image_quota import ImageQuotaManager
from aiqq.logic.models import ImageAsset, ImagePromptAuditResult, ImageUsageDecision


class FakeAuditor:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    async def audit(self, prompt, *, require_english=False):
        if self.error:
            raise self.error
        return self.result


class FakeImageService:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    async def generate(self, action, *, reference_image=None):
        self.calls.append(action)
        if self.error is not None:
            raise self.error
        return ImageAsset(b"image", "image/jpeg", 1, 1)


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


def audit(*, safe=True, effective=True):
    return ImagePromptAuditResult(
        safe=safe,
        effective=effective,
        category="safe" if safe else "adult_content",
        reason="ok" if safe and effective else "not allowed",
        suggested_prompt="" if safe and effective else "safe, sfw, landscape",
        orientation="square",
    )


class GPTImageWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_safe_prompt_generates_one_image(self):
        images = FakeImageService()
        usage = FakeImageUsage()
        result = await GPTImageWorkflow(
            prompt_auditor=FakeAuditor(audit()),
            image_service=images,
            image_usage=usage,
        ).run("cat", conversation_key="group:g:member:u")
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.images), 1)
        self.assertEqual(images.calls[0].prompt, "cat")
        self.assertEqual(usage.recorded, ["group:g:member:u"])
        self.assertEqual(usage.finished, ["group:g:member:u"])

    async def test_rejection_and_audit_failure_never_call_image_api(self):
        for auditor in (
            FakeAuditor(audit(safe=False)),
            FakeAuditor(error=ImageAuditUnavailable()),
        ):
            images = FakeImageService()
            usage = FakeImageUsage()
            result = await GPTImageWorkflow(
                prompt_auditor=auditor,
                image_service=images,
                image_usage=usage,
            ).run("prompt", conversation_key="key")
            self.assertNotEqual(result.status, "ok")
            self.assertEqual(images.calls, [])
            self.assertEqual(usage.reserved, [])

    async def test_quota_denial_never_calls_image_api(self):
        images = FakeImageService()
        usage = FakeImageUsage(ImageUsageDecision(False, "daily_limit", 3))
        result = await GPTImageWorkflow(
            prompt_auditor=FakeAuditor(audit()),
            image_service=images,
            image_usage=usage,
        ).run("cat", conversation_key="key")

        self.assertEqual(result.status, "rejected")
        self.assertIn("每日 3 张", result.full_text)
        self.assertEqual(images.calls, [])
        self.assertEqual(usage.finished, [])

    async def test_service_errors_are_safe_and_do_not_charge_quota(self):
        cases = (
            ("authentication", "管理员"),
            ("not_found", "请求失败"),
            ("rate_limited", "请求受限"),
            ("invalid_request", "未接受"),
            ("timeout", "超时"),
            ("connection", "无法连接"),
            ("upstream", "暂时异常"),
            ("invalid_response", "无法使用"),
        )
        for kind, expected in cases:
            with self.subTest(kind=kind):
                images = FakeImageService(
                    ImageGenerationUnavailable(
                        "private upstream details sk-secret", kind=kind,
                        attempt_id="attempt-123",
                    )
                )
                usage = FakeImageUsage()
                workflow = GPTImageWorkflow(
                    prompt_auditor=FakeAuditor(audit()),
                    image_service=images,
                    image_usage=usage,
                )
                with self.assertLogs("aiqq.logic.image_generation") as logs:
                    result = await workflow.run("cat", conversation_key="key")
                self.assertEqual(result.status, "unavailable")
                self.assertEqual(result.error_code, f"image_service_{kind}")
                self.assertIn(expected, result.full_text)
                self.assertLessEqual(len(result.summary), 50)
                self.assertEqual(result.images, ())
                self.assertEqual(usage.recorded, [])
                self.assertEqual(usage.finished, ["key"])
                self.assertIn("attempt_id=attempt-123", "\n".join(logs.output))
                self.assertNotIn("sk-secret", result.full_text + "\n".join(logs.output))

    async def test_failure_and_cancellation_release_real_quota_for_next_request(self):
        for error in (
            ImageGenerationUnavailable("upstream failure", kind="upstream"),
            asyncio.CancelledError(),
        ):
            with self.subTest(error=type(error).__name__):
                repository = AsyncMock()
                repository.success_count.return_value = 0
                quota = ImageQuotaManager(repository, daily_limit=100)
                images = FakeImageService(error)
                workflow = GPTImageWorkflow(
                    prompt_auditor=FakeAuditor(audit()),
                    image_service=images,
                    image_usage=quota,
                )
                if isinstance(error, asyncio.CancelledError):
                    with self.assertRaises(asyncio.CancelledError):
                        await workflow.run("cat", conversation_key="key")
                else:
                    result = await workflow.run("cat", conversation_key="key")
                    self.assertEqual(result.status, "unavailable")
                repository.increment_success.assert_not_awaited()
                images.error = None
                result = await workflow.run("cat", conversation_key="other-member")
                self.assertEqual(result.status, "ok")
                repository.increment_success.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
