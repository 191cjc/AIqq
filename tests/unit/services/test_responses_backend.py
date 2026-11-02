import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiqq.services.agents.schemas import PROMPT_AUDIT_OUTPUT_SCHEMA
from aiqq.services.ai.responses import ResponsesBackend
from aiqq.exceptions import AgentUnavailable


class FakeResponses:
    def __init__(self):
        self.requests = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(
            output_text='{"allowed":true,"category":"safe","reason":""}',
            output=[],
            usage=None,
        )


class FakeClient:
    def __init__(self):
        self.responses = FakeResponses()


class ResponsesBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_schema_is_sent_as_strict_response_format_without_storage(self):
        client = FakeClient()
        backend = ResponsesBackend(
            api_key="",
            base_url="https://api.example.com/v1",
            model="test-model",
            client=client,
        )

        result = await backend.run(purpose="chat",
            instructions="audit",
            model_input="{}",
            output_schema=PROMPT_AUDIT_OUTPUT_SCHEMA,
            enable_web_search=True,
        )

        self.assertTrue(result.text.startswith("{"))
        request = client.responses.requests[0]
        self.assertFalse(request["store"])
        self.assertNotIn("previous_response_id", request)
        self.assertEqual(
            request["text"]["format"]["schema"], PROMPT_AUDIT_OUTPUT_SCHEMA
        )
        self.assertTrue(request["text"]["format"]["strict"])
        self.assertEqual(request["tools"], [{"type": "web_search"}])

    async def test_missing_schema_fails_before_api_call(self):
        client = FakeClient()
        backend = ResponsesBackend(
            api_key="",
            base_url="https://api.example.com/v1",
            model="test-model",
            client=client,
        )
        with self.assertRaises(ValueError):
            await backend.run(purpose="chat",
                instructions="audit", model_input="{}", output_schema={}
            )
        self.assertEqual(client.responses.requests, [])

    async def test_terminal_metrics_are_normalized_and_content_free(self):
        secret_input = "private prompt https://example.test/?token=secret"
        client = FakeClient()
        client.responses.create = AsyncMock(return_value=SimpleNamespace(
            output_text='{"allowed":true,"category":"safe","reason":""}',
            output=[],
            usage={
                "input_tokens": 12,
                "input_tokens_details": {"cached_tokens": 4},
                "output_tokens": 3,
            },
        ))
        backend = ResponsesBackend(
            api_key="", base_url="https://api.example.com/v1",
            model="test-model", client=client,
        )

        with self.assertLogs("aiqq.services.ai.turn_metrics", level="INFO") as logs:
            await backend.run(
                purpose="audit",
                instructions="audit",
                model_input=secret_input,
                output_schema=PROMPT_AUDIT_OUTPUT_SCHEMA,
            )

        self.assertEqual(len(logs.output), 1)
        metric = logs.output[0]
        for field in (
            "event=ai_turn_finished", "purpose=audit", "backend=responses",
            "status=success", "input_tokens=12", "cached_input_tokens=4",
            "output_tokens=3", "provider_requests=1",
            "output_limit_mode=enforced_responses",
        ):
            self.assertIn(field, metric)
        self.assertNotIn(secret_input, metric)
        self.assertNotIn("token=secret", metric)

    async def test_failed_and_cancelled_turns_each_emit_one_terminal_metric(self):
        for mode in ("failure", "cancelled"):
            with self.subTest(mode=mode):
                started = asyncio.Event()

                async def create(**_kwargs):
                    started.set()
                    if mode == "failure":
                        raise RuntimeError("private provider body")
                    await asyncio.Future()

                client = FakeClient()
                client.responses.create = create
                backend = ResponsesBackend(
                    api_key="", base_url="https://api.example.com/v1",
                    model="test-model", client=client,
                )
                with self.assertLogs(
                    "aiqq.services.ai.turn_metrics", level="INFO"
                ) as logs:
                    task = asyncio.create_task(backend.run(
                        purpose="chat", instructions="chat",
                        model_input="private content",
                        output_schema=PROMPT_AUDIT_OUTPUT_SCHEMA,
                    ))
                    await started.wait()
                    if mode == "cancelled":
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                    else:
                        with self.assertRaises(AgentUnavailable):
                            await task
                self.assertEqual(len(logs.output), 1)
                self.assertIn("status=" + mode, logs.output[0])
                self.assertNotIn("private", logs.output[0])


if __name__ == "__main__":
    unittest.main()
