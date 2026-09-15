import unittest

from aiqq.interfaces.qq.command_router import ExplicitCommandRouter
from aiqq.interfaces.qq.commands import ParsedCommand
from aiqq.interfaces.qq.handlers import IncomingGroupMessage
from aiqq.logic.models import (
    ConversationResult,
    NovelAIPromptOptions,
    NovelAIPromptSession,
    NovelAIPromptWorkflowResult,
)


class FakeReplySender:
    def __init__(self):
        self.calls = []

    async def send(self, result, **kwargs):
        self.calls.append((result, kwargs))


class FakeImageWorkflow:
    def __init__(self, label):
        self.label = label
        self.calls = []

    async def run(self, prompt, *, conversation_key):
        self.calls.append((prompt, conversation_key))
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


class CommandRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.replies = FakeReplySender()
        self.gpt = FakeImageWorkflow("gpt")
        self.novelai = FakeImageWorkflow("novelai")
        self.prompts = FakePromptWorkflow()
        self.router = ExplicitCommandRouter(
            reply_sender=self.replies,
            gpt_image_workflow=self.gpt,
            novelai_image_workflow=self.novelai,
            novelai_prompt_workflow=self.prompts,
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
        self.assertEqual(self.novelai.calls, [("white cat", key)])

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
