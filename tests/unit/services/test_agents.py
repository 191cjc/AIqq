import json
import unittest

from aiqq.exceptions import ConversationUnavailable, PromptAuditUnavailable
from aiqq.logic.models import ConversationRequest, GroupHistoryMessage
from aiqq.logic.ports import AgentTurnResult
from aiqq.services.agents.chat import ChatAgent
from aiqq.services.agents.image_audit import ImagePromptAuditAgent
from aiqq.services.agents.novelai_prompt import NovelAIPromptAgent
from aiqq.services.agents.prompt_audit import PromptAuditAgent
from aiqq.services.agents.schemas import (
    CHAT_AGENT_OUTPUT_SCHEMA,
    IMAGE_PROMPT_AUDIT_OUTPUT_SCHEMA,
    NOVELAI_PROMPT_OPTIONS_OUTPUT_SCHEMA,
    PROMPT_AUDIT_OUTPUT_SCHEMA,
)


class FakeBackend:
    def __init__(self, text):
        self.result = AgentTurnResult(text)
        self.calls = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_agent_supplies_its_schema(self):
        audit_backend = FakeBackend(
            '{"allowed":true,"category":"safe","reason":""}'
        )
        await PromptAuditAgent(audit_backend).audit("hello")
        self.assertEqual(
            audit_backend.calls[0]["output_schema"], PROMPT_AUDIT_OUTPUT_SCHEMA
        )

        image_backend = FakeBackend(
            '{"safe":true,"effective":true,"category":"safe",'
            '"reason":"ok","suggested_prompt":"","orientation":"square"}'
        )
        await ImagePromptAuditAgent(image_backend).audit("cat")
        self.assertEqual(
            image_backend.calls[0]["output_schema"],
            IMAGE_PROMPT_AUDIT_OUTPUT_SCHEMA,
        )

        novel_backend = FakeBackend(
            '{"prompts":["safe, cat","safe, dog","safe, bird"]}'
        )
        await NovelAIPromptAgent(novel_backend).generate("animal")
        self.assertEqual(
            novel_backend.calls[0]["output_schema"],
            NOVELAI_PROMPT_OPTIONS_OUTPUT_SCHEMA,
        )

    async def test_chat_serializes_history_as_reference_material(self):
        backend = FakeBackend(
            '{"full_text":"answer","summary":"结论喵。","image_action":null,'
            '"selected_web_image_url":null,"sources":[]}'
        )
        request = ConversationRequest(
            user_input="current",
            group_history=(
                GroupHistoryMessage(
                    record_id=4,
                    role="assistant",
                    sender_name="AiQQ",
                    sent_at="2026-09-09T12:00:00+08:00",
                    content="previous answer",
                ),
            ),
            reference_material_available=True,
            conversation_key="group:user",
        )

        result = await ChatAgent(
            backend, system_prompt="system", gpt_image_skill_enabled=True
        ).run(request)

        self.assertEqual(result.full_text, "answer")
        self.assertEqual(backend.calls[0]["output_schema"], CHAT_AGENT_OUTPUT_SCHEMA)
        self.assertTrue(backend.calls[0]["enable_gpt_image_skill"])
        payload = json.loads(backend.calls[0]["model_input"])
        self.assertEqual(payload["protocol_version"], 3)
        self.assertEqual(payload["operation"], "chat")
        self.assertEqual(
            payload["reference_material"]["group_messages"][0]["role"],
            "assistant",
        )

    async def test_invalid_agent_json_maps_to_specific_unavailable_error(self):
        backend = FakeBackend("old free text")
        with self.assertRaises(PromptAuditUnavailable):
            await PromptAuditAgent(backend).audit("hello")

        request = ConversationRequest("hello", (), True, "key")
        with self.assertRaises(ConversationUnavailable):
            await ChatAgent(backend, system_prompt="system").run(request)


if __name__ == "__main__":
    unittest.main()
