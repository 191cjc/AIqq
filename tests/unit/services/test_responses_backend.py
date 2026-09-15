import unittest
from types import SimpleNamespace

from aiqq.services.agents.schemas import PROMPT_AUDIT_OUTPUT_SCHEMA
from aiqq.services.ai.responses import ResponsesBackend


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

        result = await backend.run(
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
            await backend.run(
                instructions="audit", model_input="{}", output_schema={}
            )
        self.assertEqual(client.responses.requests, [])


if __name__ == "__main__":
    unittest.main()
