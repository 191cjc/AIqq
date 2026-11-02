import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from aiqq.database import NovelAIStyleRepository, SQLiteConnection
from aiqq.interfaces.qq.command_router import ExplicitCommandRouter
from aiqq.interfaces.qq.commands import ParsedCommand, parse_command
from aiqq.interfaces.qq.handlers import IncomingGroupMessage
from aiqq.logic.novelai import NovelAIImageWorkflow
from aiqq.logic.novelai_style import NovelAIStyleWorkflow
from aiqq.logic.models import (
    ConversationResult,
    ImageAsset,
    ImagePromptAuditResult,
    ImageUsageDecision,
    NovelAIPromptOptions,
    NovelAIPromptSession,
    NovelAIPromptWorkflowResult,
)


class FakeReplySender:
    def __init__(self):
        self.calls = []
        self.start_calls = []
        self.translation_calls = []
        self.translation_error = None
        self.receipt_calls = []
        self.receipt_error = None

    async def acknowledge_image_request(self, **kwargs):
        self.receipt_calls.append(kwargs)
        if self.receipt_error:
            raise self.receipt_error

    async def send_image_start(self, **kwargs):
        self.start_calls.append(kwargs)

    async def send_novelai_translation_start(self, **kwargs):
        self.translation_calls.append(kwargs)
        if self.translation_error:
            raise self.translation_error

    async def send(self, result, **kwargs):
        self.calls.append((result, kwargs))


class FakeImageWorkflow:
    def __init__(self, label):
        self.label = label
        self.calls = []
        self.members = []

    async def run(self, prompt, *, conversation_key, member_openid="", on_image_start=None):
        self.calls.append((prompt, conversation_key))
        self.members.append(member_openid)
        return ConversationResult("ok", self.label, self.label)


class FakePromptWorkflow:
    def __init__(self):
        self.calls = []
        options = NovelAIPromptOptions(
            (
                "first, safe, sfw",
                "second, safe, sfw",
                "third, safe, sfw",
            )
        )
        self.output = NovelAIPromptWorkflowResult(
            ConversationResult("ok", "options", "options"),
            NovelAIPromptSession("token", options),
        )

    async def create(self, description, *, conversation_key):
        self.calls.append(("create", description, conversation_key))
        return self.output

    async def revise(self, token, request, *, conversation_key):
        self.calls.append(("revise", token, request, conversation_key))
        return self.output


class FakeNovelAIImageWorkflow:
    def __init__(self):
        self.calls = []

    async def run(
        self, prompt, *, conversation_key, member_openid, on_image_start=None
    ):
        self.calls.append((prompt, conversation_key, member_openid))
        return ConversationResult("ok", "novelai", "novelai")


class FakeQuickImageWorkflow:
    def __init__(self):
        self.calls = []

    async def run(
        self, description, *, conversation_key, member_openid, on_image_start=None
    ):
        self.calls.append((description, conversation_key, member_openid))
        if on_image_start is not None:
            await on_image_start()
        return ConversationResult("ok", "quick", "quick")


class FakeStyleWorkflow:
    def __init__(self):
        self.calls = []

    async def run(self, prompt, *, member_openid):
        self.calls.append((prompt, member_openid))
        return ConversationResult("ok", "style saved", "style saved")


class CommandRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.replies = FakeReplySender()
        self.gpt = FakeImageWorkflow("gpt")
        self.novelai = FakeNovelAIImageWorkflow()
        self.quick = FakeQuickImageWorkflow()
        self.prompts = FakePromptWorkflow()
        self.styles = FakeStyleWorkflow()
        self.router = ExplicitCommandRouter(
            reply_sender=self.replies,
            gpt_image_workflow=self.gpt,
            novelai_image_workflow=self.novelai,
            novelai_quick_image_workflow=self.quick,
            novelai_prompt_workflow=self.prompts,
            novelai_style_workflow=self.styles,
        )
        self.message = IncomingGroupMessage("group", "message", "member", "")

    async def test_all_image_commands_use_the_same_member_scoped_key(self):
        await self.router.handle(
            ParsedCommand("gpt_image", "cat"), self.message, first_msg_seq=1
        )
        await self.router.handle(
            ParsedCommand("novelai_image", "white cat"),
            self.message,
            first_msg_seq=1,
        )

        key = "group:group:member:member"
        self.assertEqual(self.gpt.calls, [("cat", key)])
        self.assertEqual(self.gpt.members, ["member"])
        self.assertEqual(self.novelai.calls, [("white cat", key, "member")])

    async def test_quick_image_uses_scoped_ids_and_one_receipt_before_final_result(self):
        handled = await self.router.handle(
            ParsedCommand("novelai_quick_image", "白猫\n窗边"),
            self.message,
            first_msg_seq=4,
        )

        self.assertTrue(handled)
        self.assertEqual(
            self.quick.calls,
            [("白猫\n窗边", "group:group:member:member", "member")],
        )
        self.assertEqual(self.replies.start_calls, [])
        self.assertEqual(len(self.replies.receipt_calls), 1)
        self.assertEqual(self.replies.receipt_calls[0]["msg_seq"], 4)
        self.assertEqual(self.replies.translation_calls, [])
        self.assertEqual(len(self.replies.calls), 1)
        result, context = self.replies.calls[0]
        self.assertEqual(result.full_text, "quick")
        self.assertEqual(context["first_msg_seq"], 5)
        self.assertIsNone(context["keyboard_factory"])
        self.assertTrue(context["show_generation_prompt"])
        self.assertEqual(self.prompts.calls, [])
        self.assertEqual(self.novelai.calls, [])

    async def test_chinese_novelai_image_uses_quick_generation(self):
        original_run = self.quick.run

        async def run_after_notice(*args, **kwargs):
            self.assertEqual(len(self.replies.receipt_calls), len(self.quick.calls) + 1)
            return await original_run(*args, **kwargs)

        self.quick.run = run_after_notice
        for description in ("窗边的白猫", "white cat, 坐在窗边"):
            command = parse_command(f"/NovelAI生图 {description}")
            self.assertIsNotNone(command)
            self.assertTrue(await self.router.handle(
                command, self.message, first_msg_seq=4
            ))

        key = "group:group:member:member"
        self.assertEqual(
            self.quick.calls,
            [("窗边的白猫", key, "member"), ("white cat, 坐在窗边", key, "member")],
        )
        self.assertEqual(self.novelai.calls, [])
        self.assertEqual(self.prompts.calls, [])
        self.assertEqual(self.replies.translation_calls, [])
        self.assertEqual(len(self.replies.receipt_calls), 2)
        self.assertTrue(all(
            call["msg_seq"] == 4 for call in self.replies.receipt_calls
        ))
        self.assertEqual(self.replies.start_calls, [])
        self.assertTrue(all(call["translate_prompt"] for call in self.replies.receipt_calls))
        for result, context in self.replies.calls:
            self.assertEqual(result.full_text, "quick")
            self.assertEqual(context["first_msg_seq"], 5)
            self.assertIsNone(context["keyboard_factory"])
            self.assertTrue(context["show_generation_prompt"])

    async def test_failed_receipt_still_generates_with_reserved_sequence(self):
        self.replies.receipt_error = RuntimeError("QQ unavailable")

        with self.assertLogs("aiqq.interfaces.qq.command_router", "WARNING") as logs:
            handled = await self.router.handle(
                ParsedCommand("novelai_image", "白猫"),
                self.message, first_msg_seq=1,
            )

        self.assertTrue(handled)
        self.assertIn("event=image_request_acknowledgement_failed", logs.output[0])
        self.assertEqual(self.replies.start_calls, [])
        self.assertEqual(self.replies.receipt_calls[0]["msg_seq"], 1)
        self.assertEqual(self.replies.calls[0][1]["first_msg_seq"], 2)
        self.assertTrue(self.replies.calls[0][1]["show_generation_prompt"])
        self.assertEqual(len(self.quick.calls), 1)

    async def test_failed_chinese_authoring_still_has_one_receipt(self):
        self.quick.run = AsyncMock(return_value=ConversationResult(
            "unavailable", "提示词服务不可用", "提示词服务不可用",
            error_code="novelai_prompt_unavailable",
        ))

        handled = await self.router.handle(
            ParsedCommand("novelai_image", "白猫"),
            self.message, first_msg_seq=2,
        )

        self.assertTrue(handled)
        self.assertEqual(self.replies.receipt_calls[0]["msg_seq"], 2)
        self.assertTrue(self.replies.receipt_calls[0]["translate_prompt"])
        self.assertEqual(self.replies.translation_calls, [])
        self.assertEqual(self.replies.start_calls, [])
        self.assertEqual(self.replies.calls[0][1]["first_msg_seq"], 3)

    async def test_novelai_image_without_chinese_keeps_direct_generation(self):
        for description in ("white cat", "white cat，running", "white cat 🐈", ""):
            self.assertTrue(await self.router.handle(
                ParsedCommand("novelai_image", description),
                self.message,
                first_msg_seq=1,
            ))

        key = "group:group:member:member"
        self.assertEqual(self.novelai.calls, [
            ("white cat", key, "member"),
            ("white cat，running", key, "member"),
            ("white cat 🐈", key, "member"),
            ("", key, "member"),
        ])
        self.assertEqual(self.quick.calls, [])
        self.assertEqual(len(self.replies.receipt_calls), 3)
        self.assertFalse(any(call["translate_prompt"] for call in self.replies.receipt_calls))
        self.assertEqual(self.replies.translation_calls, [])
        self.assertTrue(all(
            context["show_generation_prompt"]
            for _, context in self.replies.calls
        ))

    async def test_style_save_and_clear_use_sender_and_send_acknowledgement(self):
        for argument in ("watercolor, soft lighting", ""):
            handled = await self.router.handle(
                ParsedCommand("novelai_style", argument),
                self.message,
                first_msg_seq=2,
            )
            self.assertTrue(handled)
        self.assertEqual(
            self.styles.calls,
            [("watercolor, soft lighting", "member"), ("", "member")],
        )
        self.assertEqual(len(self.replies.calls), 2)
        self.assertEqual(self.replies.calls[0][1]["member_openid"], "member")
        self.assertEqual(self.replies.calls[0][1]["first_msg_seq"], 2)
        self.assertFalse(self.gpt.calls)
        self.assertFalse(self.novelai.calls)
        self.assertFalse(self.prompts.calls)

    async def test_manual_and_option_generation_use_current_sender_style(self):
        auditor = Mock(audit=AsyncMock(return_value=ImagePromptAuditResult(
            True, True, "safe", "safe scene", "", "square"
        )))
        generator = Mock(generate=AsyncMock(return_value=ImageAsset(
            b"image", "image/png", 1, 1
        )))
        usage = Mock(
            reserve_attempt=AsyncMock(return_value=ImageUsageDecision(True)),
            record_success=AsyncMock(),
            finish_attempt=AsyncMock(),
        )
        with tempfile.TemporaryDirectory() as directory:
            connection = SQLiteConnection(Path(directory) / "state.db")
            styles = NovelAIStyleRepository(connection)
            try:
                await styles.initialize()
                router = ExplicitCommandRouter(
                    reply_sender=self.replies,
                    gpt_image_workflow=self.gpt,
                    novelai_image_workflow=NovelAIImageWorkflow(
                        prompt_auditor=auditor,
                        image_service=generator,
                        image_usage=usage,
                        styles=styles,
                    ),
                    novelai_quick_image_workflow=self.quick,
                    novelai_prompt_workflow=self.prompts,
                    novelai_style_workflow=NovelAIStyleWorkflow(styles=styles),
                )

                async def dispatch(content, message=self.message):
                    command = parse_command(content)
                    self.assertIsNotNone(command)
                    self.assertTrue(await router.handle(
                        command, message, first_msg_seq=1
                    ))

                await dispatch("NovelAI画风 watercolor")
                await dispatch("NovelAI生图 white cat")
                await dispatch("NovelAI提示词 white cat")
                keyboard = self.replies.calls[-1][1]["keyboard_factory"](None)
                option = keyboard["content"]["rows"][0]["buttons"][0]["action"]["data"]
                self.assertNotIn("watercolor", option)

                # Options remain reusable after a style change or in another group.
                other_group = IncomingGroupMessage("second-group", "m2", "member", "")
                await dispatch("NovelAI画风 oil painting", other_group)
                await dispatch(option, other_group)
                other_member = IncomingGroupMessage("group", "m3", "other-member", "")
                await dispatch("NovelAI生图 white cat", other_member)
                await dispatch("NovelAI画风")
                await dispatch("NovelAI生图 white cat")

                expected = [
                    "watercolor, white cat",
                    "oil painting, first, safe, sfw",
                    "white cat",
                    "white cat",
                ]
                self.assertEqual(
                    [call.args[0] for call in auditor.audit.await_args_list], expected
                )
                self.assertEqual(
                    [call.args[0] for call in generator.generate.await_args_list], expected
                )
                self.assertEqual(usage.reserve_attempt.await_count, 4)
                self.assertEqual(usage.record_success.await_count, 4)
                self.assertEqual(self.replies.start_calls, [])
                self.assertEqual(len(self.replies.receipt_calls), 4)
                self.assertTrue(all(
                    call["msg_seq"] == 1 for call in self.replies.receipt_calls
                ))
                image_replies = [
                    context for result, context in self.replies.calls if result.images
                ]
                self.assertEqual(len(image_replies), 4)
                self.assertTrue(all(
                    context["first_msg_seq"] == 2 for context in image_replies
                ))
                self.assertFalse(self.gpt.calls)
            finally:
                await connection.close()

    async def test_prompt_revision_builds_session_bound_keyboard(self):
        handled = await self.router.handle(
            ParsedCommand("novelai_prompt_edit", "night", "old-token"),
            self.message,
            first_msg_seq=2,
        )

        self.assertTrue(handled)
        self.assertEqual(
            self.prompts.calls,
            [("revise", "old-token", "night", "group:group:member:member")],
        )
        kwargs = self.replies.calls[0][1]
        keyboard = kwargs["keyboard_factory"]("https://public.example/full")
        rows = keyboard["content"]["rows"]
        self.assertIn("NovelAI生图 first", rows[0]["buttons"][0]["action"]["data"])
        self.assertIn("token", rows[3]["buttons"][0]["action"]["data"])
        self.assertEqual(
            rows[4]["buttons"][0]["action"]["data"],
            "https://public.example/full",
        )


if __name__ == "__main__":
    unittest.main()
