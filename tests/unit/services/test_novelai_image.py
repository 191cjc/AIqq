import asyncio
import base64
import json
import unittest
from io import BytesIO
from unittest.mock import AsyncMock

from PIL import Image

from aiqq.exceptions import ImageGenerationUnavailable
from aiqq.services.images.novelai import (
    NOVELAI_IMAGE_SIZES,
    NOVELAI_STEPS,
    MCPClient,
    NovelAIError,
    NovelAIService,
    _decode_mcp_response,
)


def png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (12, 8), "white").save(output, format="PNG")
    return output.getvalue()


def image_result(data=None, mime_type="image/png"):
    return {
        "content": [
            {
                "type": "image",
                "mimeType": mime_type,
                "data": base64.b64encode(data or png_bytes()).decode("ascii"),
            }
        ]
    }


class FakeMCPClient:
    def __init__(self, result, *, supports_steps=True):
        self.result = result
        self.supports_steps = supports_steps
        self.calls = []
        self.closed = False

    async def list_tools(self):
        properties = {"prompt": {"type": "string"}}
        if self.supports_steps:
            properties["steps"] = {"type": "integer"}
        return {
            "tools": [
                {
                    "name": "generate_image",
                    "inputSchema": {"properties": properties},
                }
            ]
        }

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result

    async def close(self):
        self.closed = True


class NovelAIServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_absent_configuration_and_uninitialized_client_have_typed_errors(self):
        for service, kind, configured in (
            (NovelAIService(None), "not_configured", False),
            (NovelAIService(FakeMCPClient(image_result())), "not_ready", True),
        ):
            with self.subTest(kind=kind):
                self.assertEqual(service.is_configured, configured)
                self.assertFalse(service.is_ready)
                with self.assertRaises(ImageGenerationUnavailable) as raised:
                    await service.generate("white cat")
                self.assertIsInstance(raised.exception, NovelAIError)
                self.assertIsInstance(raised.exception, RuntimeError)
                self.assertEqual(raised.exception.kind, kind)

        with self.assertLogs("aiqq.services.images.novelai") as logs:
            await NovelAIService(None).initialize()
        self.assertIn("error_kind=not_configured", "\n".join(logs.output))

    async def test_missing_generation_tool_is_not_ready(self):
        for response in (
            {}, {"tools": []}, {"tools": {}},
            {"tools": [{"name": "get_account_info"}]},
        ):
            with self.subTest(response=response):
                client = FakeMCPClient(image_result())
                client.list_tools = AsyncMock(return_value=response)
                service = NovelAIService(client)
                with self.assertLogs("aiqq.services.images.novelai"):
                    self.assertFalse(await service.check_connection())
                self.assertTrue(service.is_configured)
                self.assertFalse(service.is_ready)
                with self.assertRaises(NovelAIError) as raised:
                    await service.generate("white cat")
                self.assertEqual(raised.exception.kind, "not_ready")
                self.assertEqual(client.calls, [])

    async def test_failed_discovery_clears_readiness_without_logging_remote_details(self):
        client = FakeMCPClient(image_result())
        service = NovelAIService(client)
        self.assertTrue(await service.check_connection())
        private_details = "https://private.example/mcp?token=secret private prompt"
        client.list_tools = AsyncMock(side_effect=RuntimeError(private_details))

        with self.assertLogs("aiqq.services.images.novelai") as logs:
            self.assertFalse(await service.check_connection())

        self.assertFalse(service.is_ready)
        self.assertNotIn(private_details, "\n".join(logs.output))
        self.assertIn("error_type=RuntimeError", "\n".join(logs.output))
        with self.assertRaises(NovelAIError) as raised:
            await service.generate("white cat")
        self.assertEqual(raised.exception.kind, "not_ready")

    async def test_discovery_cancellation_propagates_and_clears_readiness(self):
        client = FakeMCPClient(image_result())
        service = NovelAIService(client)
        await service.initialize()
        client.list_tools = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await service.check_connection()
        self.assertFalse(service.is_ready)

    async def test_generation_without_optional_steps_is_ready(self):
        client = FakeMCPClient(image_result(), supports_steps=False)
        service = NovelAIService(client)
        with self.assertLogs("aiqq.services.images.novelai"):
            await service.initialize()

        self.assertTrue(service.is_ready)
        await service.generate("white cat")
        self.assertNotIn("steps", client.calls[0][1])

    async def test_generation_uses_capability_and_returns_validated_asset(self):
        client = FakeMCPClient(image_result())
        service = NovelAIService(client)
        self.assertTrue(await service.check_connection())

        image = await service.generate("white cat", orientation="portrait")

        self.assertEqual((image.width, image.height), (12, 8))
        name, arguments = client.calls[0]
        self.assertEqual(name, "generate_image")
        self.assertEqual(
            (arguments["width"], arguments["height"]),
            NOVELAI_IMAGE_SIZES["portrait"],
        )
        self.assertEqual(arguments["steps"], NOVELAI_STEPS)

    async def test_invalid_or_mime_mismatched_image_is_rejected(self):
        for result in (
            image_result(b"not an image"),
            image_result(mime_type="image/jpeg"),
        ):
            service = NovelAIService(FakeMCPClient(result))
            await service.check_connection()
            with self.assertRaises(NovelAIError):
                await service.generate("white cat")

    async def test_service_has_no_environment_constructor(self):
        self.assertFalse(hasattr(NovelAIService, "from_env"))
        with self.assertRaises(ValueError):
            MCPClient(url="", token="secret", timeout_seconds=30)

    async def test_close_closes_injected_transport(self):
        client = FakeMCPClient(image_result())
        service = NovelAIService(client)
        await service.initialize()
        await service.close()
        self.assertTrue(client.closed)
        self.assertFalse(service.is_ready)

    def test_sse_response_is_decoded(self):
        payload = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        body = f"event: message\ndata: {json.dumps(payload)}\n\n".encode()
        self.assertEqual(_decode_mcp_response(body, "text/event-stream"), payload)


if __name__ == "__main__":
    unittest.main()
