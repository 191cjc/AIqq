import unittest

from aiqq.logic.models import (
    ImageAsset,
    ImagePromptAuditResult,
    ImageUsageDecision,
    NovelAIPromptOptions,
    NovelAIPromptSession,
)
from aiqq.logic.novelai import NovelAIImageWorkflow, NovelAIPromptWorkflow


OPTIONS = NovelAIPromptOptions(
    (
        "white cat, daylight, safe, sfw",
        "white cat, night city, safe, sfw",
        "white cat, portrait, safe, sfw",
    )
)


def audit(*, safe=True, effective=True, suggestion="", orientation="square"):
    return ImagePromptAuditResult(
        safe=safe,
        effective=effective,
        category="safe" if safe else "adult_content",
        reason="accepted" if safe and effective else "not allowed",
        suggested_prompt=suggestion,
        orientation=orientation,
    )


class FakeAuditor:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def audit(self, prompt, *, require_english=False):
        self.calls.append((prompt, require_english))
        return self.result


class FakeUsage:
    def __init__(self, decision=None):
        self.decision = decision or ImageUsageDecision(True)
        self.recorded = []
        self.finished = []

    async def reserve_attempt(self, key):
        return self.decision

    async def record_success(self, key):
        self.recorded.append(key)

    async def finish_attempt(self, key):
        self.finished.append(key)


class FakeNovelAI:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def generate(self, prompt, *, orientation="square"):
        self.calls.append((prompt, orientation))
        if self.error:
            raise self.error
        return ImageAsset(b"image", "image/png", 1, 1)


class FakePromptAgent:
    def __init__(self):
        self.generate_calls = []
        self.revise_calls = []

    async def generate(self, description):
        self.generate_calls.append(description)
        return OPTIONS

    async def revise(self, existing, request):
        self.revise_calls.append((existing, request))
        return OPTIONS


class FakeSessions:
    def __init__(self):
        self.loaded = None
        self.created = []

    async def create(self, conversation_key, options):
        self.created.append((conversation_key, options))
        return NovelAIPromptSession("new-token", options)

    async def load(self, conversation_key, token):
        self.load_call = (conversation_key, token)
        return self.loaded


class NovelAIWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_generation_requires_english_and_counts_success(self):
        auditor = FakeAuditor(audit(orientation="portrait"))
        usage = FakeUsage()
        image_service = FakeNovelAI()
        workflow = NovelAIImageWorkflow(
            prompt_auditor=auditor,
            image_service=image_service,
            image_usage=usage,
        )

        result = await workflow.run(
            "white cat, full body", conversation_key="key"
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(auditor.calls, [("white cat, full body", True)])
        self.assertEqual(image_service.calls, [("white cat, full body", "portrait")])
        self.assertEqual(usage.recorded, ["key"])
        self.assertEqual(usage.finished, ["key"])

    async def test_chinese_image_prompt_returns_english_suggestion(self):
        image_service = FakeNovelAI()
        usage = FakeUsage()
        workflow = NovelAIImageWorkflow(
            prompt_auditor=FakeAuditor(
                audit(suggestion="white cat, window, safe, sfw")
            ),
            image_service=image_service,
            image_usage=usage,
        )

        result = await workflow.run("窗边的白猫", conversation_key="key")

        self.assertEqual(result.status, "rejected")
        self.assertIn("必须使用英文", result.full_text)
        self.assertIn("white cat", result.full_text)
        self.assertEqual(image_service.calls, [])

    async def test_failed_generation_releases_global_slot_without_counting(self):
        usage = FakeUsage()
        workflow = NovelAIImageWorkflow(
            prompt_auditor=FakeAuditor(audit()),
            image_service=FakeNovelAI(RuntimeError("timeout")),
            image_usage=usage,
        )

        result = await workflow.run("white cat", conversation_key="key")

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(usage.recorded, [])
        self.assertEqual(usage.finished, ["key"])

    async def test_prompt_create_and_revision_issue_new_scoped_sessions(self):
        agent = FakePromptAgent()
        sessions = FakeSessions()
        workflow = NovelAIPromptWorkflow(
            prompt_auditor=FakeAuditor(audit()),
            prompt_agent=agent,
            sessions=sessions,
        )

        created = await workflow.create("一只白猫", conversation_key="key")
        sessions.loaded = created.session
        revised = await workflow.revise(
            created.session.token, "改成夜景", conversation_key="key"
        )

        self.assertEqual(created.result.status, "ok")
        self.assertIn(OPTIONS.prompts[0], created.result.full_text)
        self.assertEqual(sessions.load_call, ("key", "new-token"))
        self.assertEqual(agent.revise_calls, [(OPTIONS, "改成夜景")])
        self.assertIsNotNone(revised.session)

    async def test_expired_prompt_session_does_not_call_revision_agent(self):
        agent = FakePromptAgent()
        workflow = NovelAIPromptWorkflow(
            prompt_auditor=FakeAuditor(audit()),
            prompt_agent=agent,
            sessions=FakeSessions(),
        )

        result = await workflow.revise(
            "expired", "改成夜景", conversation_key="key"
        )

        self.assertEqual(result.result.status, "rejected")
        self.assertEqual(agent.revise_calls, [])


if __name__ == "__main__":
    unittest.main()
