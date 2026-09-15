import json
import unittest

from aiqq.exceptions import InvalidAgentOutput
from aiqq.services.agents.schemas import (
    CHAT_AGENT_OUTPUT_SCHEMA,
    IMAGE_PROMPT_AUDIT_OUTPUT_SCHEMA,
    NOVELAI_PROMPT_OPTIONS_OUTPUT_SCHEMA,
    PROMPT_AUDIT_OUTPUT_SCHEMA,
    parse_chat_agent_output,
    parse_image_prompt_audit_output,
    parse_novelai_prompt_options_output,
    parse_prompt_audit_output,
)


class AgentSchemaTests(unittest.TestCase):
    def test_every_schema_is_a_strict_object_with_required_fields(self):
        for schema in (
            PROMPT_AUDIT_OUTPUT_SCHEMA,
            CHAT_AGENT_OUTPUT_SCHEMA,
            IMAGE_PROMPT_AUDIT_OUTPUT_SCHEMA,
            NOVELAI_PROMPT_OPTIONS_OUTPUT_SCHEMA,
        ):
            self.assertEqual(schema["type"], "object")
            self.assertIs(schema["additionalProperties"], False)
            self.assertTrue(schema["required"])

    def test_prompt_audit_enforces_decision_semantics(self):
        allowed = parse_prompt_audit_output(
            '{"allowed":true,"category":"safe","reason":""}'
        )
        self.assertTrue(allowed.allowed)

        with self.assertRaises(InvalidAgentOutput):
            parse_prompt_audit_output(
                '{"allowed":false,"category":"safe","reason":"no"}'
            )

    def test_chat_rejects_free_text_old_markers_and_extra_fields(self):
        for value in (
            "ordinary free text",
            "[[aiqq_summary:old protocol]]",
            json.dumps(
                {
                    "full_text": "answer",
                    "summary": "summary",
                    "image_action": None,
                    "selected_web_image_url": None,
                    "sources": [],
                    "legacy": True,
                }
            ),
        ):
            with self.subTest(value=value), self.assertRaises(InvalidAgentOutput):
                parse_chat_agent_output(value)

    def test_chat_enforces_image_reference_and_public_urls(self):
        base = {
            "full_text": "完整回答",
            "summary": "直接结论喵。",
            "image_action": {
                "mode": "edit",
                "prompt": "make it blue",
                "source_record_id": 7,
            },
            "selected_web_image_url": "https://images.example.com/a.jpg",
            "sources": [
                {"title": "source", "url": "https://www.example.com/article"}
            ],
        }
        parsed = parse_chat_agent_output(
            json.dumps(base), allowed_image_record_ids=frozenset({7})
        )
        self.assertEqual(parsed.image_action.source_record_id, 7)

        with self.assertRaises(InvalidAgentOutput):
            parse_chat_agent_output(json.dumps(base))
        base["selected_web_image_url"] = "https://127.0.0.1/private.jpg"
        with self.assertRaises(InvalidAgentOutput):
            parse_chat_agent_output(
                json.dumps(base), allowed_image_record_ids=frozenset({7})
            )

    def test_chat_rejects_markdown_summary(self):
        value = {
            "full_text": "完整回答",
            "summary": "[查看](https://example.com)",
            "image_action": None,
            "selected_web_image_url": None,
            "sources": [],
        }
        with self.assertRaises(InvalidAgentOutput):
            parse_chat_agent_output(json.dumps(value))

    def test_image_audit_and_novelai_options_are_strict(self):
        audit = parse_image_prompt_audit_output(
            json.dumps(
                {
                    "safe": True,
                    "effective": True,
                    "category": "safe",
                    "reason": "通过",
                    "suggested_prompt": "",
                    "orientation": "square",
                }
            )
        )
        self.assertTrue(audit.safe)

        options = parse_novelai_prompt_options_output(
            json.dumps({"prompts": ["safe, cat", "safe, dog", "safe, bird"]})
        )
        self.assertEqual(len(options.prompts), 3)
        with self.assertRaises(InvalidAgentOutput):
            parse_novelai_prompt_options_output(
                json.dumps({"prompts": ["same", "same", "same"]})
            )


if __name__ == "__main__":
    unittest.main()
