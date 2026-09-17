import base64
import os
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import APIStatusError
from PIL import Image

from gpt_image_service import (
    DEFAULT_GPT_IMAGE_MODEL,
    GPT_IMAGE_OUTPUT_COMPRESSION,
    GPT_IMAGE_OUTPUT_FORMAT,
    GPT_IMAGE_INPUT_FIDELITY,
    GPT_IMAGE_QUALITY,
    GPT_IMAGE_SIZE,
    SAFE_IMAGE_INSTRUCTION,
    GPTImageError,
    GPTImageService,
)


class FakeImages:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.edit_calls = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    async def edit(self, **kwargs):
        self.edit_calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeClient:
    def __init__(self, response):
        self.images = FakeImages(response)
        self.closed = False

    async def close(self):
        self.closed = True


def source_image_bytes(size=(1254, 1254)):
    output = BytesIO()
    Image.new("RGBA", size, (30, 120, 220, 180)).save(output, format="PNG")
    return output.getvalue()


def image_response(data=None):
    if data is None:
        data = source_image_bytes()
    return SimpleNamespace(
        data=[
            SimpleNamespace(
                b64_json=base64.b64encode(data).decode("ascii"),
                url=None,
            )
        ]
    )


class GPTImageServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_generation_uses_fixed_safe_square_parameters(self):
        client = FakeClient(image_response())
        service = GPTImageService(client)

        image = await service.generate("雨夜中的白猫")

        self.assertEqual(image.mime_type, "image/jpeg")
        with Image.open(BytesIO(image.data)) as generated:
            self.assertEqual(generated.format, "JPEG")
            self.assertEqual(generated.mode, "RGB")
            self.assertEqual(generated.size, (1024, 1024))
        self.assertEqual(
            client.images.calls,
            [
                {
                    "model": DEFAULT_GPT_IMAGE_MODEL,
                    "prompt": SAFE_IMAGE_INSTRUCTION + "雨夜中的白猫",
                    "n": 1,
                    "size": GPT_IMAGE_SIZE,
                    "quality": GPT_IMAGE_QUALITY,
                    "output_format": GPT_IMAGE_OUTPUT_FORMAT,
                    "output_compression": GPT_IMAGE_OUTPUT_COMPRESSION,
                    "moderation": "auto",
                }
            ],
        )

    async def test_missing_image_data_fails(self):
        service = GPTImageService(FakeClient(SimpleNamespace(data=[])))

        with self.assertRaises(GPTImageError):
            await service.generate("landscape")

    async def test_edit_uses_reference_image_with_high_input_fidelity(self):
        client = FakeClient(image_response())
        service = GPTImageService(client)
        reference = source_image_bytes((512, 512))

        image = await service.edit(
            "保留角色外观，改成微笑",
            reference,
            "image/png",
        )

        self.assertEqual(image.mime_type, "image/jpeg")
        call = client.images.edit_calls[0]
        self.assertEqual(call["model"], DEFAULT_GPT_IMAGE_MODEL)
        self.assertEqual(
            call["image"],
            ("reference.png", reference, "image/png"),
        )
        self.assertEqual(
            call["prompt"],
            SAFE_IMAGE_INSTRUCTION + "保留角色外观，改成微笑",
        )
        self.assertEqual(call["input_fidelity"], GPT_IMAGE_INPUT_FIDELITY)
        self.assertEqual(call["size"], GPT_IMAGE_SIZE)
        self.assertEqual(call["quality"], GPT_IMAGE_QUALITY)
        self.assertEqual(call["output_format"], GPT_IMAGE_OUTPUT_FORMAT)
        self.assertEqual(
            call["output_compression"], GPT_IMAGE_OUTPUT_COMPRESSION
        )
        self.assertNotIn("moderation", call)

    async def test_edit_rejects_invalid_reference_without_api_call(self):
        client = FakeClient(image_response())
        service = GPTImageService(client)

        with self.assertRaises(GPTImageError):
            await service.edit("修改图片", b"invalid", "image/gif")

        self.assertEqual(client.images.edit_calls, [])

    async def test_invalid_base64_fails(self):
        response = SimpleNamespace(
            data=[SimpleNamespace(b64_json="not-base64!", url=None)]
        )
        service = GPTImageService(FakeClient(response))

        with self.assertRaises(GPTImageError):
            await service.generate("landscape")

    async def test_valid_base64_with_invalid_image_fails(self):
        service = GPTImageService(FakeClient(image_response(b"not-an-image")))

        with self.assertRaises(GPTImageError):
            await service.generate("landscape")

    async def test_status_error_logs_safe_detail_and_request_id(self):
        request = httpx.Request(
            "POST", "https://example.com/v1/images/generations"
        )
        response = httpx.Response(
            503,
            request=request,
            headers={"x-request-id": "req-image-123"},
            json={
                "error": {
                    "message": "upstream unavailable for sk-sensitive-value",
                    "type": "server_error",
                    "code": "upstream_unavailable",
                    "prompt": "must not be logged",
                }
            },
        )
        error = APIStatusError(
            "upstream failed",
            response=response,
            body=response.json(),
        )
        service = GPTImageService(FakeClient(error))

        with self.assertLogs("gpt_image_service", level="WARNING") as logs:
            with self.assertRaises(GPTImageError):
                await service.generate("private user prompt")

        output = "\n".join(logs.output)
        self.assertIn("HTTP 503", output)
        self.assertIn("request_id=req-image-123", output)
        self.assertIn("upstream_unavailable", output)
        self.assertIn("<redacted>", output)
        self.assertNotIn("sk-sensitive-value", output)
        self.assertNotIn("must not be logged", output)
        self.assertNotIn("private user prompt", output)

    async def test_close_closes_openai_client(self):
        client = FakeClient(image_response())

        await GPTImageService(client).close()

        self.assertTrue(client.closed)

    def test_from_env_reuses_chat_credentials_by_default(self):
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "chat-key",
                "OPENAI_BASE_URL": "https://example.com/v1",
            },
            clear=True,
        ):
            with patch("gpt_image_service.AsyncOpenAI") as client_class:
                service = GPTImageService.from_env()

        client_class.assert_called_once_with(
            api_key="chat-key",
            base_url="https://example.com/v1",
            timeout=240.0,
            max_retries=2,
        )
        self.assertEqual(service.model, DEFAULT_GPT_IMAGE_MODEL)


if __name__ == "__main__":
    unittest.main()
