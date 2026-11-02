import json
import unittest

from aiqq.exceptions import InvalidAgentOutput
from aiqq.services.agents.schemas import (
    CHAT_AGENT_OUTPUT_SCHEMA,
    IMAGE_PROMPT_AUDIT_OUTPUT_SCHEMA,
    NOVELAI_PROMPT_OPTIONS_OUTPUT_SCHEMA,
    NOVELAI_TAG_QUERIES_OUTPUT_SCHEMA,
    PROMPT_AUDIT_OUTPUT_SCHEMA,
    parse_chat_agent_output,
    parse_image_prompt_audit_output,
    parse_novelai_prompt_options_output,
    parse_novelai_tag_queries_output,
    parse_prompt_audit_output,
)


class AgentSchemaTests(unittest.TestCase):
    def test_every_schema_is_a_strict_object_with_required_fields(self):
        for schema in (
            PROMPT_AUDIT_OUTPUT_SCHEMA,
            CHAT_AGENT_OUTPUT_SCHEMA,
            IMAGE_PROMPT_AUDIT_OUTPUT_SCHEMA,
            NOVELAI_PROMPT_OPTIONS_OUTPUT_SCHEMA,
            NOVELAI_TAG_QUERIES_OUTPUT_SCHEMA,
        ):
            self.assertEqual(schema["type"], "object")
            self.assertIs(schema["additionalProperties"], False)
            self.assertTrue(schema["required"])

    def test_prompt_audit_enforces_decision_semantics(self):
        allowed = parse_prompt_audit_output(
            '{"allowed":true,"category":"safe","reason":""}'
        )
        self.assertTrue(allowed.allowed)

        for category in ("adult_content", "persona_override", "too_complex"):
            with self.subTest(category=category):
                rejected = parse_prompt_audit_output(json.dumps({
                    "allowed": False, "category": category, "reason": "拒绝原因",
                }))
                self.assertFalse(rejected.allowed)
                self.assertEqual(rejected.category, category)

        for output in (
            '{"allowed":false,"category":"safe","reason":"no"}',
            '{"allowed":false,"category":"too_many_questions","reason":"no"}',
            '{"allowed":false,"category":"complex_research","reason":"no"}',
        ):
            with self.subTest(output=output), self.assertRaises(InvalidAgentOutput):
                parse_prompt_audit_output(output)

    def test_chat_rejects_free_text_old_markers_and_extra_fields(self):
        for value in (
            "ordinary free text",
            "[[aiqq_summary:old protocol]]",
            json.dumps(
                {
                    "full_text": "answer",
                    "summary": "summary",
                    "image_action": None,
                    "selected_web_image_urls": [],
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
                "width": None,
                "height": None,
            },
            "selected_web_image_urls": ["https://images.example.com/a.jpg"],
            "sources": [
                {"title": "source", "url": "https://www.example.com/article"}
            ],
        }
        parsed = parse_chat_agent_output(
            json.dumps(base), allowed_image_record_ids=frozenset({7})
        )
        self.assertEqual(parsed.image_action.source_record_id, 7)
        self.assertEqual(parsed.image_action.target_dimensions, (1024, 1024))

        base["image_action"].update(width=1920, height=1080)
        parsed = parse_chat_agent_output(
            json.dumps(base), allowed_image_record_ids=frozenset({7})
        )
        self.assertEqual(parsed.image_action.target_dimensions, (1920, 1080))

        with self.assertRaises(InvalidAgentOutput):
            parse_chat_agent_output(json.dumps(base))
        base["selected_web_image_urls"] = ["https://127.0.0.1/private.jpg"]
        with self.assertRaises(InvalidAgentOutput):
            parse_chat_agent_output(
                json.dumps(base), allowed_image_record_ids=frozenset({7})
            )

    def test_chat_image_dimensions_are_required_nullable_and_bounded(self):
        base = {
            "full_text": "正在生成图片。",
            "summary": "正在生成图片喵。",
            "image_action": {
                "mode": "generate",
                "prompt": "cat",
                "source_record_id": None,
                "width": None,
                "height": None,
            },
            "selected_web_image_urls": [],
            "sources": [],
        }
        for width, height in (
            (None, None),
            (256, 256),
            (2048, 2048),
            (1920, 1080),
        ):
            with self.subTest(width=width, height=height):
                value = {**base, "image_action": {
                    **base["image_action"], "width": width, "height": height,
                }}
                action = parse_chat_agent_output(json.dumps(value)).image_action
                expected = (1024, 1024) if width is None else (width, height)
                self.assertEqual(action.target_dimensions, expected)

        invalid_actions = (
            {key: value for key, value in base["image_action"].items() if key != "width"},
            {**base["image_action"], "width": 1024, "height": None},
            {**base["image_action"], "width": None, "height": 1024},
            {**base["image_action"], "width": 255, "height": 1024},
            {**base["image_action"], "width": 2049, "height": 1024},
            {**base["image_action"], "width": True, "height": 1024},
            {**base["image_action"], "width": 1024.0, "height": 1024},
        )
        for action in invalid_actions:
            with self.subTest(action=action), self.assertRaises(InvalidAgentOutput):
                parse_chat_agent_output(json.dumps({**base, "image_action": action}))

        action_schema = CHAT_AGENT_OUTPUT_SCHEMA["$defs"]["_ImageActionOutput"]
        self.assertIn("width", action_schema["required"])
        self.assertIn("height", action_schema["required"])

    def test_chat_web_image_selections_are_ordered_distinct_and_bounded(self):
        base = {
            "full_text": "找到所需图片。",
            "summary": "找到所需图片。",
            "image_action": None,
            "selected_web_image_urls": [],
            "sources": [],
        }
        urls = [f"https://images.example.com/{index}.jpg" for index in range(6)]
        for count in (0, 1, 3, 5):
            with self.subTest(count=count):
                parsed = parse_chat_agent_output(json.dumps({
                    **base, "selected_web_image_urls": urls[:count],
                }))
                self.assertEqual(parsed.selected_web_image_urls, tuple(urls[:count]))

        parsed = parse_chat_agent_output(json.dumps({
            **base, "selected_web_image_urls": [urls[2], urls[0], urls[2]],
        }))
        self.assertEqual(parsed.selected_web_image_urls, (urls[2], urls[0]))
        for value in (
            urls, None, urls[0], [False], ["http://images.example.com/a.jpg"],
            [urls[0], "https://127.0.0.1/private.jpg"],
            ["https://user:secret@images.example.com/a.jpg"],
        ):
            with self.subTest(value=value), self.assertRaises(InvalidAgentOutput):
                parse_chat_agent_output(json.dumps({
                    **base, "selected_web_image_urls": value,
                }))
        with self.assertRaises(InvalidAgentOutput):
            parse_chat_agent_output(json.dumps({
                key: value for key, value in base.items()
                if key != "selected_web_image_urls"
            }))
        self.assertEqual(
            CHAT_AGENT_OUTPUT_SCHEMA["properties"]["selected_web_image_urls"]["maxItems"],
            5,
        )

    def test_chat_rejects_markdown_summary(self):
        value = {
            "full_text": "完整回答",
            "summary": "[查看](https://example.com)",
            "image_action": None,
            "selected_web_image_urls": [],
            "sources": [],
        }
        with self.assertRaises(InvalidAgentOutput):
            parse_chat_agent_output(json.dumps(value))

    def test_image_audit_and_novelai_options_are_strict(self):
        base = {
            "safe": True,
            "effective": True,
            "category": "safe",
            "reason": "通过",
            "suggested_prompt": "",
            "orientation": "square",
        }
        for orientation in ("square", "portrait", "landscape"):
            with self.subTest(orientation=orientation):
                audit = parse_image_prompt_audit_output(
                    json.dumps({**base, "orientation": orientation})
                )
                self.assertTrue(audit.safe)
                self.assertEqual(audit.orientation, orientation)
        for invalid in (
            {key: value for key, value in base.items() if key != "orientation"},
            {**base, "orientation": None},
            {**base, "orientation": "auto"},
            {**base, "orientation": "1024x1536"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(InvalidAgentOutput):
                parse_image_prompt_audit_output(json.dumps(invalid))

        options = parse_novelai_prompt_options_output(
            json.dumps({"prompts": ["safe, cat", "safe, dog", "safe, bird"]})
        )
        self.assertEqual(len(options.prompts), 3)
        with self.assertRaises(InvalidAgentOutput):
            parse_novelai_prompt_options_output(
                json.dumps({"prompts": ["same", "same", "same"]})
            )

    def test_novelai_tag_queries_normalize_and_deduplicate_in_order(self):
        self.assertEqual(
            parse_novelai_tag_queries_output(json.dumps({
                "queries": [
                    " Blue  Hair ", "blue_hair", "1girl", "cat_(animal)",
                    "fate/stay_night", "vocaloid+",
                    "Hirogaru_Sky!_Precure", "bang_dream!",
                ]
            })),
            (
                "blue_hair", "1girl", "cat_(animal)", "fate/stay_night", "vocaloid+",
                "hirogaru_sky!_precure", "bang_dream!",
            ),
        )
        self.assertEqual(
            len(parse_novelai_tag_queries_output(json.dumps({
                "queries": [f"scene_{index}" for index in range(8)]
            }))),
            8,
        )

    def test_novelai_tag_queries_reject_unbounded_or_invalid_plans(self):
        invalid = [
            {},
            {"queries": []},
            {"queries": ["cat"] * 9},
            {"queries": ["cat"], "tools": []},
            {"queries": "cat"},
            {"queries": [False]},
            {"queries": ["x" * 81]},
            {"queries": [" " + "x" * 80]},
        ]
        invalid.extend({"queries": [query]} for query in (
            "", "   ", "猫", "blue\nhair", "\tcat", "cat\r", "cat\x7f",
            "cat,dog", "rating:safe", "cat*", "~cat", "-cat", "cat|dog",
            "cat&dog", "cat;dog", "cat=dog", "123", "!cat", "cat?", "cat\\dog",
        ))
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(InvalidAgentOutput):
                parse_novelai_tag_queries_output(json.dumps(value))


if __name__ == "__main__":
    unittest.main()
