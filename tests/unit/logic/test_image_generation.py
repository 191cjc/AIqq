import asyncio
import unittest
from unittest.mock import AsyncMock

from aiqq.exceptions import ImageAuditUnavailable, ImageGenerationUnavailable
from aiqq.logic.image_audit import ImageAuditPolicy
from aiqq.logic.image_generation import GPTImageWorkflow
from aiqq.logic.image_quota import ImageQuotaManager
from aiqq.logic.models import (
    ImageAction,
    ImageAsset,
    ImagePromptAuditResult,
    ImageUsageDecision,
)


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


def audit(*, safe=True, effective=True, orientation="square"):
    return ImagePromptAuditResult(
        safe=safe,
        effective=effective,
        category="safe" if safe else "adult_content",
        reason="ok" if safe and effective else "not allowed",
        suggested_prompt="" if safe and effective else "safe, sfw, landscape",
        orientation=orientation,
    )


class GPTImageWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_exempt_sender_skips_audit_and_still_uses_normal_generation_and_quota(self):
        auditor = AsyncMock()
        auditor.audit.side_effect = ImageAuditUnavailable("audit offline")
        images = FakeImageService()
        usage = FakeImageUsage()
        workflow = GPTImageWorkflow(
            prompt_auditor=auditor, image_service=images, image_usage=usage,
            audit_policy=ImageAuditPolicy(frozenset({"trusted"})),
        )
        started = AsyncMock()
        result = await workflow.run(
            " white cat ", conversation_key="group:g:member:trusted",
            member_openid="trusted", on_image_start=started,
        )
        self.assertEqual(result.status, "ok")
        auditor.audit.assert_not_awaited()
        started.assert_awaited_once_with()
        self.assertEqual(images.calls, [ImageAction("generate", "white cat")])
        self.assertEqual(usage.reserved, ["group:g:member:trusted"])
        self.assertEqual(usage.recorded, usage.reserved)
        self.assertEqual(usage.finished, usage.reserved)
        # A matching-looking key cannot exempt a different or absent sender.
        for member in ("other", ""):
            result = await workflow.run(
                "white cat", conversation_key="group:g:member:trusted", member_openid=member,
            )
            self.assertEqual(result.error_code, "image_audit_unavailable")
        self.assertEqual(auditor.audit.await_count, 2)
        self.assertEqual(len(images.calls), 1)

    async def test_exempt_gpt_requests_still_reject_empty_input_and_exhausted_quota(self):
        auditor = AsyncMock()
        images = FakeImageService()
        usage = FakeImageUsage(ImageUsageDecision(False, "daily_limit", 100))
        workflow = GPTImageWorkflow(
            prompt_auditor=auditor, image_service=images, image_usage=usage,
            audit_policy=ImageAuditPolicy(frozenset({"trusted"})),
        )
        empty = await workflow.run(" \n ", conversation_key="key", member_openid="trusted")
        denied = await workflow.run("white cat", conversation_key="key", member_openid="trusted")
        self.assertEqual(empty.status, "rejected")
        self.assertEqual(denied.status, "rejected")
        auditor.audit.assert_not_awaited()
        self.assertEqual(usage.reserved, ["key"])
        self.assertEqual(usage.recorded, [])
        self.assertEqual(images.calls, [])

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
        self.assertEqual(images.calls[0].target_dimensions, (1024, 1024))
        self.assertEqual(usage.recorded, ["group:g:member:u"])
        self.assertEqual(usage.finished, ["group:g:member:u"])

    async def test_explicit_command_ignores_audit_orientation_and_defaults_square(self):
        for orientation in (
            "square",
            "portrait",
            "landscape",
        ):
            with self.subTest(orientation=orientation):
                images = FakeImageService()
                result = await GPTImageWorkflow(
                    prompt_auditor=FakeAuditor(audit(orientation=orientation)),
                    image_service=images,
                    image_usage=FakeImageUsage(),
                ).run("cat", conversation_key="key")
                self.assertEqual(result.status, "ok")
                self.assertEqual(images.calls[0].target_dimensions, (1024, 1024))

    def test_image_action_validates_custom_dimensions(self):
        self.assertEqual(
            ImageAction("generate", "cat", width=256, height=2048).target_dimensions,
            (256, 2048),
        )
        self.assertEqual(
            ImageAction("generate", "cat", width=2048, height=2048).target_dimensions,
            (2048, 2048),
        )
        for width, height in (
            (None, 1024),
            (1024, None),
            (255, 1024),
            (2049, 1024),
            (True, 1024),
            (1024.0, 1024),
        ):
            with self.subTest(width=width, height=height), self.assertRaises(ValueError):
                ImageAction("generate", "cat", width=width, height=height)

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

    async def test_rejected_image_summary_shows_reason_and_preserves_long_detail(self):
        reason = "画面仅包含参数，缺少可绘制的主体或场景。" * 4
        auditor = FakeAuditor(ImagePromptAuditResult(
            safe=True, effective=False, category="safe", reason=reason,
            suggested_prompt="white cat, safe, sfw", orientation="square",
        ))
        images = FakeImageService()
        usage = FakeImageUsage()

        result = await GPTImageWorkflow(
            prompt_auditor=auditor, image_service=images, image_usage=usage,
        ).run("parameters only", conversation_key="key")

        self.assertEqual(result.status, "rejected")
        self.assertIn(reason, result.full_text)
        self.assertIn("可改为：white cat, safe, sfw", result.full_text)
        self.assertTrue(result.summary.startswith("审核未通过：画面仅包含参数"))
        self.assertTrue(result.summary.endswith("…"))
        self.assertLessEqual(len(result.summary), 50)
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
