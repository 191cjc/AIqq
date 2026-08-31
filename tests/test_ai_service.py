import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, patch

from ai_service import (
    AIService,
    RESEARCH_STAGE_TOOL_NAME,
    clean_prompt,
    normalize_research_stage,
)


class FakeResponses:
    def __init__(self, response):
        responses = response if isinstance(response, list) else [response]
        self.responses = [
            SimpleNamespace(output_text=item)
            if isinstance(item, str)
            else item
            for item in responses
        ]
        self.calls = []
        self.create_calls = []
        self.stream_calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        self.create_calls.append(kwargs)
        return self._next_response()

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        self.stream_calls.append(kwargs)
        return FakeStream(self._next_response())

    def _next_response(self):
        if len(self.responses) == 1:
            return self.responses[0]
        return self.responses.pop(0)


class FakeStream:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return None

    async def get_final_response(self):
        return self.response


class FakeClient:
    def __init__(self, output_text: str):
        self.responses = FakeResponses(output_text)
        self.closed = False

    async def close(self):
        self.closed = True


def make_service(client=None, max_reply_chars=1800, web_search_enabled=True):
    return AIService(
        client,
        model="gpt-test",
        system_prompt="测试提示词",
        max_output_tokens=256,
        summary_max_output_tokens=128,
        max_reply_chars=max_reply_chars,
        max_concurrent=2,
        web_search_enabled=web_search_enabled,
    )


class AIServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_from_env_uses_180_second_timeout_by_default(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            with patch("ai_service.AsyncOpenAI") as client_class:
                service = AIService.from_env()

        client_class.assert_called_once_with(
            api_key="test-key",
            base_url="https://api.airoo.cc/v1",
            timeout=180.0,
            max_retries=2,
        )
        self.assertEqual(service.total_timeout_seconds, 270)

    def test_research_stage_is_one_sentence_and_at_most_60_chars(self):
        self.assertEqual(
            normalize_research_stage("  已找到官方文档的主要方向。  后面不应保留。"),
            "已找到官方文档的主要方向。",
        )
        long_stage = normalize_research_stage("这" * 80)
        self.assertEqual(len(long_stage), 60)
        self.assertTrue(long_stage.endswith("。"))

    def test_clean_prompt_removes_leading_bot_mention(self):
        self.assertEqual(clean_prompt("  <@!123456>  你好"), "你好")
        self.assertEqual(clean_prompt("普通文本"), "普通文本")

    async def test_unconfigured_service_returns_setup_message(self):
        result = await make_service().answer("你好")
        self.assertFalse(result.success)
        self.assertIn("OPENAI_API_KEY", result.text)

    async def test_prompt_is_forwarded_to_responses_api(self):
        client = FakeClient("模型回复")
        service = make_service(client)

        result = await service.answer("用户问题")

        self.assertTrue(result.success)
        self.assertEqual(result.text, "模型回复")
        self.assertEqual(client.responses.create_calls, [])
        self.assertEqual(
            client.responses.stream_calls,
            [
                {
                    "model": "gpt-test",
                    "instructions": ANY,
                    "input": "用户问题",
                    "max_output_tokens": 256,
                    "tools": [{"type": "web_search"}],
                    "include": ["web_search_call.action.sources"],
                }
            ],
        )
        self.assertIn(
            "联网搜索工具", client.responses.stream_calls[0]["instructions"]
        )

    async def test_web_search_can_be_disabled(self):
        client = FakeClient("模型回复")
        service = make_service(client, web_search_enabled=False)

        await service.answer("用户问题")

        call = client.responses.calls[0]
        self.assertNotIn("tools", call)
        self.assertEqual(call["instructions"], "测试提示词")

    async def test_missing_inline_web_source_is_appended(self):
        citation = SimpleNamespace(
            type="url_citation",
            title="Example",
            url="https://example.com/article",
        )
        content = SimpleNamespace(annotations=[citation])
        message = SimpleNamespace(type="message", content=[content])
        response = SimpleNamespace(output_text="联网结果", output=[message])

        result = await make_service(FakeClient(response)).answer("查询最新信息")

        self.assertIn("来源：", result.text)
        self.assertIn("https://example.com/article", result.text)

    async def test_research_stage_is_delivered_then_model_continues(self):
        stage_call = SimpleNamespace(
            type="function_call",
            name=RESEARCH_STAGE_TOOL_NAME,
            call_id="call-stage-1",
            arguments=json.dumps(
                {
                    "message": (
                        "目前资料主要指向接口超时问题。"
                        "这句不应该出现在节点消息中。"
                    )
                },
                ensure_ascii=False,
            ),
        )
        stage_response = SimpleNamespace(
            id="response-stage-1",
            output_text="",
            output=[stage_call],
        )
        final_response = SimpleNamespace(
            id="response-final",
            output_text="这是最终完整回答。",
            output=[],
        )
        client = FakeClient([stage_response, final_response])
        stages = []

        async def collect_stage(message):
            stages.append(message)

        result = await make_service(client).answer(
            "查询最新接口限制",
            on_stage=collect_stage,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.text, "这是最终完整回答。")
        self.assertEqual(stages, ["目前资料主要指向接口超时问题。"])
        self.assertEqual(len(client.responses.stream_calls), 2)
        first_call, continuation = client.responses.stream_calls
        self.assertEqual(first_call["tools"][1]["name"], RESEARCH_STAGE_TOOL_NAME)
        self.assertNotIn("previous_response_id", continuation)
        self.assertEqual(
            continuation["input"][-1]["type"], "function_call_output"
        )
        self.assertEqual(
            continuation["input"][-1]["call_id"], "call-stage-1"
        )
        self.assertTrue(
            json.loads(continuation["input"][-1]["output"])["delivered"]
        )
        self.assertEqual(
            continuation["input"][0],
            {"role": "user", "content": "查询最新接口限制"},
        )
        self.assertEqual(continuation["input"][1]["type"], "function_call")

    async def test_duplicate_research_stage_is_not_delivered_twice(self):
        def stage_response(response_id, call_id):
            return SimpleNamespace(
                id=response_id,
                output_text="",
                output=[
                    SimpleNamespace(
                        type="function_call",
                        name=RESEARCH_STAGE_TOOL_NAME,
                        call_id=call_id,
                        arguments=json.dumps(
                            {"message": "目前资料主要指向接口超时问题。"},
                            ensure_ascii=False,
                        ),
                    )
                ],
            )

        client = FakeClient(
            [
                stage_response("response-1", "call-1"),
                stage_response("response-2", "call-2"),
                SimpleNamespace(output_text="最终回答。", output=[]),
            ]
        )
        stages = []

        async def collect_stage(message):
            stages.append(message)

        result = await make_service(client).answer("查询", on_stage=collect_stage)

        self.assertTrue(result.success)
        self.assertEqual(stages, ["目前资料主要指向接口超时问题。"])
        duplicate_output = json.loads(
            client.responses.stream_calls[2]["input"][-1]["output"]
        )
        self.assertFalse(duplicate_output["delivered"])
        self.assertEqual(duplicate_output["reason"], "duplicate_message")

    async def test_at_most_four_research_stages_are_delivered(self):
        calls = [
            SimpleNamespace(
                type="function_call",
                name=RESEARCH_STAGE_TOOL_NAME,
                call_id=f"call-{number}",
                arguments=json.dumps(
                    {"message": f"已经确认第{number}个检索方向。"},
                    ensure_ascii=False,
                ),
            )
            for number in range(1, 6)
        ]
        client = FakeClient(
            [
                SimpleNamespace(output_text="", output=calls),
                SimpleNamespace(output_text="最终回答。", output=[]),
            ]
        )
        stages = []

        async def collect_stage(message):
            stages.append(message)

        result = await make_service(client).answer("查询", on_stage=collect_stage)

        self.assertTrue(result.success)
        self.assertEqual(len(stages), 4)
        continuation = client.responses.stream_calls[1]
        self.assertEqual(continuation["tools"], [{"type": "web_search"}])
        fifth_output = json.loads(continuation["input"][-1]["output"])
        self.assertEqual(fifth_output["reason"], "stage_limit_reached")

    async def test_long_reply_is_truncated(self):
        client = FakeClient("测" * 300)
        result = await make_service(client, max_reply_chars=200).answer("问题")

        self.assertEqual(len(result.text), 200)
        self.assertTrue(result.text.endswith("[回复过长，已截断]"))

    async def test_history_and_summary_are_sent_as_context(self):
        client = FakeClient("继续回复")
        service = make_service(client)

        await service.answer(
            "继续说",
            history=(
                {"role": "user", "content": "前一个问题"},
                {"role": "assistant", "content": "前一个回答"},
            ),
            summary="用户喜欢简短回答。",
        )

        call = client.responses.calls[0]
        self.assertIn("用户喜欢简短回答", call["instructions"])
        self.assertEqual(
            call["input"],
            [
                {"role": "user", "content": "前一个问题"},
                {"role": "assistant", "content": "前一个回答"},
                {"role": "user", "content": "继续说"},
            ],
        )

    async def test_summary_uses_accumulated_summary_and_messages(self):
        client = FakeClient("新的累计摘要")
        service = make_service(client)

        result = await service.summarize(
            "旧摘要",
            (
                {"role": "user", "content": "问题"},
                {"role": "assistant", "content": "回答"},
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(client.responses.stream_calls, [])
        self.assertEqual(len(client.responses.create_calls), 1)
        call = client.responses.calls[0]
        self.assertIn("旧摘要", call["input"])
        self.assertIn("用户：问题", call["input"])
        self.assertEqual(call["max_output_tokens"], 128)
        self.assertNotIn("tools", call)

    async def test_safe_image_prompt_is_approved_without_chat_history(self):
        client = FakeClient(
            '{"safe":true,"category":"safe","reason":"普通风景"}'
        )
        service = make_service(client)

        result = await service.moderate_image_prompt("a quiet mountain landscape")

        self.assertTrue(result.available)
        self.assertTrue(result.safe)
        call = client.responses.calls[0]
        self.assertIn("安全审核器", call["instructions"])
        self.assertEqual(call["max_output_tokens"], 128)
        self.assertNotIn("history", call)
        self.assertNotIn("tools", call)

    async def test_adult_image_prompt_is_rejected(self):
        client = FakeClient(
            '{"safe":false,"category":"adult_content","reason":"成人内容"}'
        )

        result = await make_service(client).moderate_image_prompt("unsafe prompt")

        self.assertTrue(result.available)
        self.assertFalse(result.safe)
        self.assertEqual(result.category, "adult_content")

    async def test_english_image_prompt_is_effective(self):
        client = FakeClient(
            '{"effective":true,"suggested_prompt":"","reason":"画面明确"}'
        )

        result = await make_service(client).review_image_prompt(
            "cat, sitting by a window"
        )

        self.assertTrue(result.available)
        self.assertTrue(result.effective)
        self.assertFalse(result.contains_chinese)
        call = client.responses.calls[0]
        self.assertIn("有效性检查器", call["instructions"])
        self.assertNotIn("tools", call)

    async def test_chinese_image_prompt_gets_english_suggestion(self):
        client = FakeClient(
            '{"effective":true,"suggested_prompt":"white cat, by a window",'
            '"reason":"包含中文"}'
        )

        result = await make_service(client).review_image_prompt(
            "一只坐在窗边的白猫"
        )

        self.assertTrue(result.available)
        self.assertTrue(result.contains_chinese)
        self.assertEqual(result.suggested_prompt, "white cat, by a window")
        model_input = json.loads(client.responses.calls[0]["input"])
        self.assertTrue(model_input["contains_chinese"])

    async def test_invalid_quality_response_fails_closed(self):
        result = await make_service(FakeClient("VALID")).review_image_prompt(
            "cat"
        )

        self.assertFalse(result.available)
        self.assertFalse(result.effective)

    async def test_invalid_moderation_response_fails_closed(self):
        result = await make_service(FakeClient("SAFE")).moderate_image_prompt(
            "landscape"
        )

        self.assertFalse(result.available)
        self.assertFalse(result.safe)

    async def test_client_is_closed(self):
        client = FakeClient("回复")
        service = make_service(client)

        await service.close()

        self.assertTrue(client.closed)


if __name__ == "__main__":
    unittest.main()
