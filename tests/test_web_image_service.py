import asyncio
import unittest
from io import BytesIO

from PIL import Image

from codex_image import CodexGeneratedImage
from web_image_service import WebImageError, WebImageService


def make_png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (16, 16), "green").save(output, format="PNG")
    return output.getvalue()


class CandidateWebImageService(WebImageService):
    def __init__(self):
        super().__init__(timeout_seconds=5)
        self.calls = []
        self.image = CodexGeneratedImage(make_png(), "image/png")

    async def _download(self, url: str) -> CodexGeneratedImage:
        self.calls.append(url)
        if "broken" in url:
            raise WebImageError("broken")
        return self.image


class FakeContent:
    def __init__(self, data: bytes):
        self.data = data

    async def iter_chunked(self, _size: int):
        yield self.data


class FakeResponse:
    def __init__(self, data: bytes, content_type: str = "image/png"):
        self.status = 200
        self.headers = {"Content-Type": content_type}
        self.content_length = len(data)
        self.content = FakeContent(data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return None


class FakeSession:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class WebImageServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_url_validation_requires_public_https_without_credentials(self):
        self.assertEqual(
            WebImageService._validated_url("https://images.example/cat.jpg"),
            "https://images.example/cat.jpg",
        )
        for url in (
            "http://images.example/cat.jpg",
            "https://user:secret@images.example/cat.jpg",
            "https://127.0.0.1/cat.jpg",
            "https://10.0.0.1/cat.jpg",
            "https://[::1]/cat.jpg",
        ):
            with self.subTest(url=url), self.assertRaises(WebImageError):
                WebImageService._validated_url(url)

    async def test_failed_candidate_falls_back_to_next_ranked_image(self):
        service = CandidateWebImageService()

        image = await service.download_best(
            (
                "https://broken.example/first.jpg",
                "https://images.example/second.jpg",
                "https://images.example/third.jpg",
            )
        )

        self.assertIs(image, service.image)
        self.assertEqual(
            service.calls,
            [
                "https://broken.example/first.jpg",
                "https://images.example/second.jpg",
            ],
        )

    async def test_download_validates_bytes_as_a_real_supported_image(self):
        service = WebImageService(timeout_seconds=5)
        session = FakeSession(FakeResponse(make_png()))

        async def get_session():
            return session

        service._get_session = get_session
        image = await service._download("https://images.example/cat.png")

        self.assertEqual(image.mime_type, "image/png")
        self.assertEqual(image.data, make_png())
        self.assertFalse(session.calls[0][1]["allow_redirects"])

    async def test_download_rejects_non_image_content_type(self):
        service = WebImageService(timeout_seconds=5)
        session = FakeSession(FakeResponse(make_png(), "text/html"))

        async def get_session():
            return session

        service._get_session = get_session

        with self.assertRaises(WebImageError):
            await service._download("https://images.example/not-image")

    async def test_total_timeout_returns_no_image(self):
        service = WebImageService(timeout_seconds=0)

        async def slow_download(_url: str):
            await asyncio.sleep(1)

        service._download = slow_download

        self.assertIsNone(
            await service.download_best(("https://images.example/slow.jpg",))
        )


if __name__ == "__main__":
    unittest.main()
