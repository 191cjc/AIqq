import asyncio
import json
import unittest
from unittest.mock import AsyncMock

from aiqq.exceptions import (
    ImageAuditUnavailable,
    ImageGenerationUnavailable,
    NovelAIPromptInvalidOutput,
    NovelAIPromptUnavailable,
    NovelAITagSearchUnavailable,
)
from aiqq.logic.image_quota import ImageQuotaManager
from aiqq.logic.models import (
    ImageAsset,
    ImagePromptAuditResult,
    ImageUsageDecision,
    NovelAIPromptOptions,
    NovelAIPromptSession,
)
from aiqq.logic.novelai import (
    NovelAIImageWorkflow,
    NovelAIPromptWorkflow,
    NovelAIQuickImageWorkflow,
)
from aiqq.logic.ports import AgentTurnResult
from aiqq.services.agents.novelai_prompt import NovelAIPromptAgent
from aiqq.services.novelai_tags import NovelAITagSearchService


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


class FakeNovelAI:
    def __init__(self, error=None, events=None):
        self.error = error
        self.events = events
        self.calls = []

    async def generate(self, prompt, *, orientation="square"):
        self.calls.append((prompt, orientation))
        if self.events is not None:
            self.events.append("generate")
        if self.error:
            raise self.error
        return ImageAsset(b"image", "image/png", 1, 1)


class FakeStyles:
    def __init__(self, styles=None, error=None):
        self.styles = styles or {}
        self.error = error
        self.calls = []

    async def load(self, member_openid):
        self.calls.append(member_openid)
        if self.error:
            raise self.error
        return self.styles.get(member_openid)


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
    async def test_style_is_loaded_once_and_combined_before_audit_and_generation(self):
        styles = FakeStyles({"member": "{watercolor},\nsoft  lighting"})
        auditor = FakeAuditor(audit(orientation="landscape"))
        images = FakeNovelAI()
        usage = FakeUsage()
        workflow = NovelAIImageWorkflow(
            prompt_auditor=auditor, image_service=images,
            image_usage=usage, styles=styles,
        )

        result = await workflow.run(
            "  white cat  ", conversation_key="group:first:member:member",
            member_openid="member",
        )

        combined = "{watercolor},\nsoft  lighting, white cat"
        self.assertEqual(result.status, "ok")
        self.assertEqual(styles.calls, ["member"])
        self.assertEqual(auditor.calls, [(combined, True)])
        self.assertEqual(images.calls, [(combined, "landscape")])
        self.assertEqual(usage.reserved, ["group:first:member:member"])
        self.assertEqual(usage.recorded, usage.reserved)
        self.assertEqual(usage.finished, usage.reserved)

    async def test_current_sender_style_follows_user_across_groups_and_changes(self):
        styles = FakeStyles({"member": "watercolor", "other": "pixel art"})
        images = FakeNovelAI()
        usage = FakeUsage()
        workflow = NovelAIImageWorkflow(
            prompt_auditor=FakeAuditor(audit()), image_service=images,
            image_usage=usage, styles=styles,
        )
        for group in ("one", "two"):
            await workflow.run(
                OPTIONS.prompts[0], conversation_key=f"group:{group}:member:member",
                member_openid="member",
            )
        styles.styles["member"] = "oil painting"
        await workflow.run(
            OPTIONS.prompts[0], conversation_key="group:two:member:member",
            member_openid="member",
        )
        await workflow.run(
            "white cat", conversation_key="group:two:member:other",
            member_openid="other",
        )
        styles.styles.pop("member")
        await workflow.run(
            OPTIONS.prompts[0], conversation_key="group:two:member:member",
            member_openid="member",
        )
        self.assertEqual(
            [prompt for prompt, _ in images.calls],
            [
                f"watercolor, {OPTIONS.prompts[0]}",
                f"watercolor, {OPTIONS.prompts[0]}",
                f"oil painting, {OPTIONS.prompts[0]}",
                "pixel art, white cat",
                OPTIONS.prompts[0],
            ],
        )

    async def test_empty_image_prompt_does_not_generate_from_style_alone(self):
        styles = FakeStyles({"member": "watercolor"})
        auditor = FakeAuditor(audit())
        images = FakeNovelAI()
        usage = FakeUsage()
        workflow = NovelAIImageWorkflow(
            prompt_auditor=auditor, image_service=images,
            image_usage=usage, styles=styles,
        )
        result = await workflow.run(
            " \n ", conversation_key="key", member_openid="member"
        )
        self.assertEqual(result.status, "rejected")
        self.assertEqual(styles.calls, [])
        self.assertEqual(auditor.calls, [])
        self.assertEqual(usage.reserved, [])
        self.assertEqual(images.calls, [])

    async def test_style_read_failure_stops_before_audit_and_quota(self):
        auditor = FakeAuditor(audit())
        images = FakeNovelAI()
        usage = FakeUsage()
        workflow = NovelAIImageWorkflow(
            prompt_auditor=auditor, image_service=images,
            image_usage=usage,
            styles=FakeStyles(error=RuntimeError("private-member secret-style")),
        )
        with self.assertLogs("aiqq.logic.novelai") as logs:
            result = await workflow.run(
                "white cat", conversation_key="key", member_openid="private-member"
            )
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.error_code, "novelai_style_unavailable")
        self.assertIn("画风读取失败", result.full_text)
        self.assertEqual(auditor.calls, [])
        self.assertEqual(usage.reserved, [])
        self.assertEqual(images.calls, [])
        self.assertNotIn("private-member", "\n".join(logs.output))
        self.assertNotIn("secret-style", "\n".join(logs.output))

    async def test_rejected_combined_prompt_never_reaches_quota_or_generation(self):
        for result in (
            audit(safe=False, suggestion="watercolor, white cat"),
            audit(effective=False, suggestion="watercolor, white cat"),
        ):
            with self.subTest(audit=result):
                auditor = FakeAuditor(result)
                usage = FakeUsage()
                images = FakeNovelAI()
                workflow = NovelAIImageWorkflow(
                    prompt_auditor=auditor, image_service=images,
                    image_usage=usage, styles=FakeStyles({"member": "unsafe style"}),
                )
                response = await workflow.run(
                    "white cat", conversation_key="key", member_openid="member"
                )
                self.assertEqual(response.status, "rejected")
                self.assertEqual(auditor.calls, [("unsafe style, white cat", True)])
                self.assertEqual(usage.reserved, [])
                self.assertEqual(images.calls, [])

    async def test_configuration_failures_have_safe_fixed_copy_and_release_quota(self):
        for kind, text, code in (
            (
                "not_configured", "NovelAI 生图服务未配置，请联系管理员。",
                "novelai_service_not_configured",
            ),
            (
                "not_ready", "NovelAI 生图服务连接未就绪，请稍后再试。",
                "novelai_service_not_ready",
            ),
            (
                "private-unknown-kind", "NovelAI 图片生成暂时不可用，请稍后再试。",
                "novelai_generation_unavailable",
            ),
        ):
            with self.subTest(kind=kind):
                usage = FakeUsage()
                workflow = NovelAIImageWorkflow(
                    prompt_auditor=FakeAuditor(audit()),
                    image_service=FakeNovelAI(
                        ImageGenerationUnavailable("secret upstream details", kind=kind)
                    ),
                    image_usage=usage,
                    styles=FakeStyles(),
                )
                with self.assertLogs("aiqq.logic.novelai") as logs:
                    result = await workflow.run(
                        "white cat", conversation_key="key", member_openid="member"
                    )
                self.assertEqual(result.status, "unavailable")
                self.assertEqual(result.full_text, text)
                self.assertEqual(result.summary, text)
                self.assertEqual(result.error_code, code)
                self.assertEqual(result.images, ())
                self.assertEqual(usage.recorded, [])
                self.assertEqual(usage.finished, ["key"])
                self.assertNotIn("secret", "\n".join(logs.output) + result.full_text)
                self.assertNotIn("private-unknown-kind", "\n".join(logs.output))

    async def test_failure_and_cancellation_release_real_quota_for_next_request(self):
        for error in (
            ImageGenerationUnavailable("missing config", kind="not_configured"),
            ImageGenerationUnavailable("not initialized", kind="not_ready"),
            asyncio.CancelledError(),
        ):
            with self.subTest(error=type(error).__name__):
                repository = AsyncMock()
                repository.success_count.return_value = 0
                quota = ImageQuotaManager(repository, daily_limit=100)
                images = FakeNovelAI(error)
                workflow = NovelAIImageWorkflow(
                    prompt_auditor=FakeAuditor(audit()),
                    image_service=images,
                    image_usage=quota,
                    styles=FakeStyles(),
                )
                if isinstance(error, asyncio.CancelledError):
                    with self.assertRaises(asyncio.CancelledError):
                        await workflow.run(
                            "white cat", conversation_key="key", member_openid="member"
                        )
                else:
                    result = await workflow.run(
                        "white cat", conversation_key="key", member_openid="member"
                    )
                    self.assertEqual(result.status, "unavailable")
                repository.increment_success.assert_not_awaited()
                images.error = None
                result = await workflow.run(
                    "white cat", conversation_key="next-member", member_openid="member"
                )
                self.assertEqual(result.status, "ok")
                repository.increment_success.assert_awaited_once()

    async def test_image_generation_requires_english_and_counts_success(self):
        auditor = FakeAuditor(audit(orientation="portrait"))
        usage = FakeUsage()
        image_service = FakeNovelAI()
        workflow = NovelAIImageWorkflow(
            prompt_auditor=auditor,
            image_service=image_service,
            image_usage=usage,
            styles=FakeStyles(),
        )

        result = await workflow.run(
            "white cat, full body", conversation_key="key", member_openid="member"
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
            styles=FakeStyles(),
        )

        result = await workflow.run(
            "窗边的白猫", conversation_key="key", member_openid="member"
        )

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
            styles=FakeStyles(),
        )

        result = await workflow.run(
            "white cat", conversation_key="key", member_openid="member"
        )

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(usage.recorded, [])
        self.assertEqual(usage.finished, ["key"])

    async def test_quick_image_authors_then_generates_first_option_without_session(self):
        prompt_auditor = FakeAuditor(audit())
        prompt_agent = FakePromptAgent()
        sessions = FakeSessions()
        prompt_workflow = NovelAIPromptWorkflow(
            prompt_auditor=prompt_auditor,
            prompt_agent=prompt_agent,
            sessions=sessions,
        )
        events = []
        image_auditor = FakeAuditor(audit(orientation="portrait"))
        images = FakeNovelAI(events=events)
        usage = FakeUsage()
        image_workflow = NovelAIImageWorkflow(
            prompt_auditor=image_auditor,
            image_service=images,
            image_usage=usage,
            styles=FakeStyles({"member": "watercolor"}),
        )
        workflow = NovelAIQuickImageWorkflow(
            prompt_workflow=prompt_workflow,
            image_workflow=image_workflow,
        )

        async def on_image_start():
            events.append("start")

        result = await workflow.run(
            "  一只白猫\n坐在窗边  ",
            conversation_key="group:group:member:member",
            member_openid="member",
            on_image_start=on_image_start,
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(
            prompt_auditor.calls,
            [("一只白猫\n坐在窗边", False)],
        )
        self.assertEqual(prompt_agent.generate_calls, ["一只白猫\n坐在窗边"])
        self.assertEqual(sessions.created, [])
        self.assertEqual(
            image_auditor.calls,
            [(f"watercolor, {OPTIONS.prompts[0]}", True)],
        )
        self.assertEqual(
            images.calls,
            [(f"watercolor, {OPTIONS.prompts[0]}", "portrait")],
        )
        self.assertEqual(events, ["start", "generate"])
        self.assertEqual(usage.reserved, ["group:group:member:member"])
        self.assertEqual(usage.recorded, usage.reserved)
        self.assertEqual(usage.finished, usage.reserved)

    async def test_empty_quick_image_stops_before_prompt_or_image_work(self):
        auditor = FakeAuditor(audit())
        agent = FakePromptAgent()
        sessions = FakeSessions()
        styles = FakeStyles({"member": "watercolor"})
        usage = FakeUsage()
        images = FakeNovelAI()
        workflow = NovelAIQuickImageWorkflow(
            prompt_workflow=NovelAIPromptWorkflow(
                prompt_auditor=auditor,
                prompt_agent=agent,
                sessions=sessions,
            ),
            image_workflow=NovelAIImageWorkflow(
                prompt_auditor=auditor,
                image_service=images,
                image_usage=usage,
                styles=styles,
            ),
        )

        result = await workflow.run(
            " \n ", conversation_key="key", member_openid="member"
        )

        self.assertEqual(result.status, "rejected")
        self.assertIn("/NovelAI快速生图", result.full_text)
        self.assertEqual(auditor.calls, [])
        self.assertEqual(agent.generate_calls, [])
        self.assertEqual(sessions.created, [])
        self.assertEqual(styles.calls, [])
        self.assertEqual(usage.reserved, [])
        self.assertEqual(images.calls, [])

    async def test_quick_prompt_audit_failures_stop_before_authoring_and_image(self):
        for audit_result, audit_error, expected_status in (
            (audit(safe=False, suggestion="safe white cat"), None, "rejected"),
            (None, ImageAuditUnavailable("private"), "unavailable"),
        ):
            with self.subTest(expected_status=expected_status):
                auditor = AsyncMock()
                if audit_error is not None:
                    auditor.audit.side_effect = audit_error
                else:
                    auditor.audit.return_value = audit_result
                agent = AsyncMock()
                styles = FakeStyles()
                usage = FakeUsage()
                images = FakeNovelAI()
                workflow = NovelAIQuickImageWorkflow(
                    prompt_workflow=NovelAIPromptWorkflow(
                        prompt_auditor=auditor,
                        prompt_agent=agent,
                        sessions=FakeSessions(),
                    ),
                    image_workflow=NovelAIImageWorkflow(
                        prompt_auditor=FakeAuditor(audit()),
                        image_service=images,
                        image_usage=usage,
                        styles=styles,
                    ),
                )

                result = await workflow.run(
                    "white cat", conversation_key="key", member_openid="member"
                )

                self.assertEqual(result.status, expected_status)
                agent.generate.assert_not_awaited()
                self.assertEqual(styles.calls, [])
                self.assertEqual(usage.reserved, [])
                self.assertEqual(images.calls, [])

    async def test_quick_prompt_failures_stop_before_image_work(self):
        cases = (
            (
                NovelAITagSearchUnavailable("private"),
                "novelai_tag_search_unavailable",
                "Danbooru 标签查询失败",
            ),
            (
                NovelAIPromptInvalidOutput(stage="planning"),
                "novelai_prompt_invalid_output",
                "标签查询词生成格式有误",
            ),
            (
                NovelAIPromptInvalidOutput(stage="authoring"),
                "novelai_prompt_invalid_output",
                "提示词生成格式有误",
            ),
            (
                NovelAIPromptUnavailable("private"),
                "novelai_prompt_unavailable",
                "提示词服务暂时不可用",
            ),
        )
        for error, error_code, expected_text in cases:
            with self.subTest(error=error):
                agent = AsyncMock()
                agent.generate.side_effect = error
                sessions = FakeSessions()
                styles = FakeStyles()
                usage = FakeUsage()
                images = FakeNovelAI()
                workflow = NovelAIQuickImageWorkflow(
                    prompt_workflow=NovelAIPromptWorkflow(
                        prompt_auditor=FakeAuditor(audit()),
                        prompt_agent=agent,
                        sessions=sessions,
                    ),
                    image_workflow=NovelAIImageWorkflow(
                        prompt_auditor=FakeAuditor(audit()),
                        image_service=images,
                        image_usage=usage,
                        styles=styles,
                    ),
                )

                result = await workflow.run(
                    "white cat", conversation_key="key", member_openid="member"
                )

                self.assertEqual(result.status, "unavailable")
                self.assertEqual(result.error_code, error_code)
                self.assertIn(expected_text, result.full_text)
                self.assertEqual(sessions.created, [])
                self.assertEqual(styles.calls, [])
                self.assertEqual(usage.reserved, [])
                self.assertEqual(images.calls, [])

    async def test_quick_prompt_cancellation_propagates_without_image_work(self):
        agent = AsyncMock()
        agent.generate.side_effect = asyncio.CancelledError()
        images = FakeNovelAI()
        workflow = NovelAIQuickImageWorkflow(
            prompt_workflow=NovelAIPromptWorkflow(
                prompt_auditor=FakeAuditor(audit()),
                prompt_agent=agent,
                sessions=FakeSessions(),
            ),
            image_workflow=NovelAIImageWorkflow(
                prompt_auditor=FakeAuditor(audit()),
                image_service=images,
                image_usage=FakeUsage(),
                styles=FakeStyles(),
            ),
        )

        with self.assertRaises(asyncio.CancelledError):
            await workflow.run(
                "white cat", conversation_key="key", member_openid="member"
            )
        self.assertEqual(images.calls, [])

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

    async def test_tag_lookup_failure_is_specific_and_does_not_create_session(self):
        for operation in ("create", "revise"):
            with self.subTest(operation=operation):
                agent = AsyncMock()
                error = NovelAITagSearchUnavailable("private remote details")
                agent.generate.side_effect = error
                agent.revise.side_effect = error
                sessions = FakeSessions()
                sessions.loaded = NovelAIPromptSession("old-token", OPTIONS)
                workflow = NovelAIPromptWorkflow(
                    prompt_auditor=FakeAuditor(audit()),
                    prompt_agent=agent, sessions=sessions,
                )
                if operation == "create":
                    output = await workflow.create("white cat", conversation_key="key")
                else:
                    output = await workflow.revise(
                        "old-token", "night scene", conversation_key="key"
                    )
                result = output.result
                self.assertEqual(result.status, "unavailable")
                self.assertEqual(result.error_code, "novelai_tag_search_unavailable")
                self.assertEqual(result.full_text, "Danbooru 标签查询失败，请稍后重试。")
                self.assertEqual(result.summary, result.full_text)
                self.assertIsNone(output.session)
                self.assertEqual(sessions.created, [])
                self.assertNotIn("private", result.full_text)

    async def test_invalid_model_outputs_are_specific_and_never_create_sessions(self):
        valid_plan = json.dumps({"queries": ["hirogaru_sky!_precure"]})
        cases = (
            (
                "planning",
                [json.dumps({"queries": ["hirogaru_sky!_precure", "rating:private"]})],
                "NovelAI 标签查询词生成格式有误，请重试或调整描述。",
                0,
            ),
            (
                "authoring",
                [valid_plan, "https://private.test/?token=secret"],
                "NovelAI 提示词生成格式有误，请重试或调整描述。",
                1,
            ),
            (
                "authoring",
                [valid_plan, json.dumps({"prompts": ["safe, 猫", "safe, dog", "safe, bird"]})],
                "NovelAI 提示词生成格式有误，请重试或调整描述。",
                1,
            ),
        )
        for operation in ("create", "revise"):
            for stage, responses, message, expected_lookups in cases:
                with self.subTest(operation=operation, stage=stage, responses=responses):
                    backend = AsyncMock()
                    backend.run.side_effect = [AgentTurnResult(value) for value in responses]
                    transport = AsyncMock()
                    transport.call_tool.return_value = {"structuredContent": {"result": []}}
                    sessions = FakeSessions()
                    sessions.loaded = NovelAIPromptSession("old-token", OPTIONS)
                    workflow = NovelAIPromptWorkflow(
                        prompt_auditor=FakeAuditor(audit()),
                        prompt_agent=NovelAIPromptAgent(
                            backend, tag_search=NovelAITagSearchService(transport)
                        ),
                        sessions=sessions,
                    )
                    with self.assertLogs("aiqq.services.agents.novelai_prompt"):
                        if operation == "create":
                            output = await workflow.create(
                                "光之美少女的索拉", conversation_key="private-group-member"
                            )
                        else:
                            output = await workflow.revise(
                                "old-token", "光之美少女的索拉", conversation_key="private-group-member"
                            )
                    self.assertEqual(output.result.status, "unavailable")
                    self.assertEqual(output.result.error_code, "novelai_prompt_invalid_output")
                    self.assertEqual(output.result.full_text, message)
                    self.assertEqual(output.result.summary, message)
                    self.assertIsNone(output.session)
                    self.assertEqual(sessions.created, [])
                    self.assertEqual(sessions.loaded, NovelAIPromptSession("old-token", OPTIONS))
                    self.assertEqual(transport.call_tool.await_count, expected_lookups)
                    self.assertEqual(backend.run.await_count, len(responses))

    async def test_unavailable_model_keeps_service_error_without_creating_session(self):
        for operation in ("create", "revise"):
            with self.subTest(operation=operation):
                agent = AsyncMock()
                error = NovelAIPromptUnavailable("private backend details")
                agent.generate.side_effect = error
                agent.revise.side_effect = error
                sessions = FakeSessions()
                sessions.loaded = NovelAIPromptSession("old-token", OPTIONS)
                workflow = NovelAIPromptWorkflow(
                    prompt_auditor=FakeAuditor(audit()), prompt_agent=agent, sessions=sessions
                )
                if operation == "create":
                    output = await workflow.create("cat", conversation_key="key")
                else:
                    output = await workflow.revise("old-token", "cat", conversation_key="key")
                self.assertEqual(output.result.status, "unavailable")
                self.assertEqual(output.result.error_code, "novelai_prompt_unavailable")
                self.assertEqual(output.result.full_text, "NovelAI 提示词服务暂时不可用，请稍后再试。")
                self.assertEqual(output.result.summary, output.result.full_text)
                self.assertIsNone(output.session)
                self.assertEqual(sessions.created, [])


if __name__ == "__main__":
    unittest.main()
