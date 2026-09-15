import base64
import json
import unittest
from io import BytesIO

from PIL import Image

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
        await NovelAIService(client).close()
        self.assertTrue(client.closed)

    def test_sse_response_is_decoded(self):
        payload = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        body = f"event: message\ndata: {json.dumps(payload)}\n\n".encode()
        self.assertEqual(_decode_mcp_response(body, "text/event-stream"), payload)


if __name__ == "__main__":
    unittest.main()
