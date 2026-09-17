import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from aiqq.database import GroupMessageRepository, SQLiteConnection
from aiqq.exceptions import AgentContextTooLarge
from aiqq.logic.conversation import ConversationWorkflow
from aiqq.logic.models import PromptAuditResult
from aiqq.logic.ports import AgentTurnResult
from aiqq.services.agents.chat import ChatAgent
from aiqq.services.chat_read import GroupChatReadService, IMAGE_READ_MESSAGES
from aiqq.services.images.web import WebImageError
from aiqq.services.images.chat_decode import decode_chat_image
from tests.unit.services.test_chat_read import image_bytes


ANSWER = json.dumps({"full_text": "answer", "summary": "回答", "image_action": None,
                     "selected_web_image_url": None, "sources": []})


class CompleteContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.connection = SQLiteConnection(Path(self.temp.name) / "messages.db")
        self.repository = GroupMessageRepository(self.connection)
        await self.repository.initialize()
        self.backend = AsyncMock()
        self.backend.run.return_value = AgentTurnResult(ANSWER)
        self.audit = AsyncMock()
        self.audit.audit.return_value = PromptAuditResult(True, "safe", "")
        self.workflow = ConversationWorkflow(
            prompt_auditor=self.audit, history_repository=self.repository,
            conversation_agent=ChatAgent(self.backend, system_prompt="system"),
            image_usage=AsyncMock(), history_char_limit=10,
        )

    async def asyncTearDown(self):
        await self.repository.close()
        self.temp.cleanup()

    async def event(self, i, group="g"):
        content = "行一\n\n  行二\t" + "正文" * 150
        event = {"op": 0, "s": i, "t": "GROUP_MESSAGE_CREATE", "id": f"e{i}",
                 "unknown_envelope": {"empty": None},
                 "d": {"id": f"m{i}", "group_openid": group, "content": content,
                       "timestamp": "2026-09-16T01:00:00+00:00",
                       "author": {"member_openid": "member", "new_identity": [0, False, None]},
                       "nested_unknown": {"reference": {"items": [None, False, 0, ""]}},
                       "attachments": [{"url": f"https://example.test/{n}.png", "content_type": "image/png"} for n in range(23)]}}
        await self.repository.add_gateway_event(event["t"], event, raw_text=json.dumps(event))
        return event

    async def run_workflow(self, message="m52"):
        return await self.workflow.run(group_id="g", before_message_id=message,
                                       conversation_key="key", user_input="查看记录")

    async def test_full_fifty_plus_current_flow_keeps_unknown_columns_payload_and_whitespace(self):
        async with self.connection.transaction() as connection:
            await connection.execute("ALTER TABLE group_messages ADD COLUMN future_flag TEXT DEFAULT 'new field'")
        for i in range(1, 53):
            await self.event(i)
        await self.event(99, group="other")
        result = await self.run_workflow()
        self.assertEqual(result.status, "ok")
        payload = json.loads(self.backend.run.await_args.kwargs["model_input"])
        records = payload["reference_material"]["group_messages"]
        self.assertEqual(len(records), 50)
        self.assertEqual([r["record"]["message_id"] for r in records], [f"m{i}" for i in range(2, 52)])
        self.assertGreater(sum(len(r["record"]["content"]) for r in records), 10000)
        self.assertTrue(payload["reference_material"]["scope"]["has_more"])
        for record in records:
            expected = await self.repository.get_full_record("g", record["record"]["record_id"])
            self.assertEqual(record, expected)
            self.assertEqual(record["record"]["future_flag"], "new field")
            self.assertIn("\n\n  ", record["record"]["content"])
            self.assertEqual(len(record["payload"]["attachments"]), 23)
            self.assertEqual(record["payload"]["nested_unknown"]["reference"]["items"], [None, False, 0, ""])
        current = payload["current_message"]
        self.assertEqual(current, await self.repository.get_full_message("g", "m52"))
        self.assertEqual(len(current["payload"]["attachments"]), 23)

    async def test_context_capacity_error_is_explicit_without_reduced_retry(self):
        await self.event(1)
        self.backend.run.side_effect = AgentContextTooLarge()
        result = await self.run_workflow("m1")
        self.assertEqual(result.error_code, "conversation_context_too_large")
        self.assertIn("完整聊天记录超过模型容量", result.summary)
        self.assertEqual(self.backend.run.await_count, 1)

    async def test_read_session_is_bound_to_group_and_closed_on_cancellation(self):
        from aiqq.logic.models import ConversationRequest
        factory = unittest.mock.Mock()
        session = AsyncMock()
        session.image_record_ids = frozenset()
        session.all_image_read_failures = ()
        factory.create_session.return_value = session
        self.backend.run.side_effect = asyncio.CancelledError()
        agent = ChatAgent(self.backend, system_prompt="system", chat_read_service=factory)
        with self.assertRaises(asyncio.CancelledError):
            await agent.run(ConversationRequest("图片", (), True, "key", group_id="g"))
        factory.create_session.assert_called_once_with("g")
        self.assertIs(self.backend.run.await_args.kwargs["chat_read_session"], session)
        session.close.assert_awaited_once()

    async def test_actual_read_errors_cannot_disappear_from_model_summary(self):
        await self.event(1)
        await self.event(2)
        expired = WebImageError("private-url", kind="not_found", status_code=404)
        denied = WebImageError("private-url", kind="access_denied", status_code=403)
        timeout = WebImageError("private-url", kind="timeout")
        image = decode_chat_image(image_bytes())
        for outcomes, expected in (
            ([expired], IMAGE_READ_MESSAGES["expired"]),
            ([timeout], IMAGE_READ_MESSAGES["timeout"]),
            ([expired, denied], "部分链接过期或失效、访问受限，请重新发送图片。"),
            ([expired, image], "model summary"),
        ):
            with self.subTest(expected=expected):
                downloader = AsyncMock()
                downloader.download_for_read.side_effect = outcomes
                service = GroupChatReadService(repository=self.repository, downloader=downloader)
                model_text = "独立的历史回答。" * 20

                async def respond(**options):
                    session = options["chat_read_session"]
                    for index in range(len(outcomes)):
                        await session.read_image(index + 1)
                    return AgentTurnResult(json.dumps({
                        "full_text": model_text, "summary": "model summary", "image_action": None,
                        "selected_web_image_url": None, "sources": [],
                    }))

                self.backend.run.side_effect = respond
                self.workflow._conversation_agent = ChatAgent(
                    self.backend, system_prompt="system", chat_read_service=service,
                )
                result = await self.run_workflow("m2")
                self.assertEqual(result.status, "ok")
                self.assertEqual(result.summary, expected)
                self.assertIn(model_text, result.full_text)
                self.assertNotIn("private-url", result.full_text)
                self.assertLessEqual(len(result.summary), 50)
                if expected != "model summary":
                    for outcome in outcomes:
                        kind = "expired" if outcome.kind == "not_found" else outcome.kind
                        self.assertIn(IMAGE_READ_MESSAGES[kind], result.full_text)
