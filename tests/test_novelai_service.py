import base64
import json
import unittest

from novelai_service import (
    NOVELAI_CFG_RESCALE,
    NOVELAI_IMAGE_SIZES,
    NOVELAI_MODEL,
    NOVELAI_STEPS,
    SAFE_NEGATIVE_PROMPT,
    SAFE_PROMPT_PREFIX,
    NovelAIError,
    NovelAIService,
    _decode_mcp_response,
)


class FakeMCPClient:
    def __init__(self, result, supports_steps=False):
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


def image_result(data=b"png-data"):
    return {
        "content": [
            {
                "type": "image",
                "mimeType": "image/png",
                "data": base64.b64encode(data).decode("ascii"),
            }
        ]
    }


class NovelAIServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_fixed_negative_prompt_only_covers_adult_content(self):
        included_tags = (
            "nsfw",
            "explicit",
            "nude",
            "sex",
            "suggestive",
            "fetish",
            "lingerie",
            "child sexualization",
        )
        for tag in included_tags:
            self.assertIn(tag, SAFE_NEGATIVE_PROMPT)

    def test_fixed_negative_prompt_has_no_quality_or_rendering_tags(self):
        omitted_tags = (
            "text",
            "watermark",
            "deformed",
            "bad anatomy",
            "bad hands",
            "worst quality",
            "lowres",
            "blurry",
            "monochrome",
            "film grain",
            "1990s (style)",
            "pointy ears",
            "amputee",
            "chromatic aberration",
            "chibi",
            "muscular male",
        )
        prompt_tags = {tag.strip() for tag in SAFE_NEGATIVE_PROMPT.split(",")}
        for tag in omitted_tags:
            self.assertNotIn(tag, prompt_tags)

    async def test_generation_uses_only_fixed_text_to_image_parameters(self):
        client = FakeMCPClient(image_result(), supports_steps=True)
        service = NovelAIService(client)
        await service.check_connection()

        image = await service.generate("1girl, blue hair --seed 999")

        self.assertEqual(image.data, b"png-data")
        name, arguments = client.calls[0]
        self.assertEqual(name, "generate_image")
        self.assertEqual(NOVELAI_IMAGE_SIZES["square"], (1024, 1024))
        self.assertEqual(NOVELAI_STEPS, 28)
        self.assertEqual(NOVELAI_CFG_RESCALE, 0.5)
        self.assertEqual(arguments["steps"], 28)
        self.assertTrue(
            arguments["prompt"].startswith("rating:general, safe, sfw,")
        )
        self.assertEqual(
            arguments,
            {
                "prompt": f"{SAFE_PROMPT_PREFIX}, 1girl, blue hair --seed 999",
                "negative_prompt": SAFE_NEGATIVE_PROMPT,
                "model": NOVELAI_MODEL,
                "width": 1024,
                "height": 1024,
                "seed": None,
                "quality_toggle": False,
                "variety_boost": True,
                "cfg_rescale": NOVELAI_CFG_RESCALE,
                "steps": NOVELAI_STEPS,
            },
        )

    async def test_generation_uses_portrait_dimensions(self):
        client = FakeMCPClient(image_result(), supports_steps=True)
        service = NovelAIService(client)
        await service.check_connection()

        await service.generate("1girl, full body", orientation="portrait")

        arguments = client.calls[0][1]
        self.assertEqual(
            (arguments["width"], arguments["height"]),
            NOVELAI_IMAGE_SIZES["portrait"],
        )

    async def test_generation_uses_landscape_dimensions(self):
        client = FakeMCPClient(image_result(), supports_steps=True)
        service = NovelAIService(client)
        await service.check_connection()

        await service.generate("1girl, lying down", orientation="landscape")

        arguments = client.calls[0][1]
        self.assertEqual(
            (arguments["width"], arguments["height"]),
            NOVELAI_IMAGE_SIZES["landscape"],
        )

    async def test_unsupported_orientation_is_rejected(self):
        client = FakeMCPClient(image_result(), supports_steps=True)
        service = NovelAIService(client)
        await service.check_connection()

        with self.assertRaises(NovelAIError):
            await service.generate("white cat", orientation="diagonal")

    async def test_server_default_is_used_when_server_hides_steps(self):
        client = FakeMCPClient(image_result(), supports_steps=False)
        service = NovelAIService(client)
        self.assertTrue(await service.check_connection())

        await service.generate("landscape")

        self.assertNotIn("steps", client.calls[0][1])

    async def test_missing_image_fails(self):
        client = FakeMCPClient({"content": []}, supports_steps=True)
        service = NovelAIService(client)
        await service.check_connection()

        with self.assertRaises(NovelAIError):
            await service.generate("landscape")

    async def test_close_closes_project_client(self):
        client = FakeMCPClient(image_result())
        await NovelAIService(client).close()
        self.assertTrue(client.closed)

    def test_sse_response_is_decoded(self):
        payload = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        body = f"event: message\ndata: {json.dumps(payload)}\n\n".encode()

        self.assertEqual(
            _decode_mcp_response(body, "text/event-stream"), payload
        )


if __name__ == "__main__":
    unittest.main()
