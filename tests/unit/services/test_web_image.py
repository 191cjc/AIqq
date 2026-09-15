import asyncio
import unittest
from io import BytesIO

from PIL import Image

from aiqq.services.images.web import (
    MAX_REDIRECTS,
    WebImageError,
    WebImageService,
    validate_public_https_url,
)


def make_png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (16, 12), "green").save(output, format="PNG")
    return output.getvalue()


class FakeContent:
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, _size: int):
        for chunk in self._chunks:
            yield chunk


class FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        body: bytes = b"",
        content_type: str = "image/png",
        location: str | None = None,
        content_length: int | None = None,
    ) -> None:
        self.status = status
        self.headers = {"Content-Type": content_type}
        if location is not None:
            self.headers["Location"] = location
        self.content_length = len(body) if content_length is None else content_length
        self.content = FakeContent((body,))

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return None


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self._responses.pop(0)


class WebImageServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_url_requires_public_https_without_credentials_or_custom_port(self):
        valid = "https://images.example/item.png?width=100"
        self.assertEqual(validate_public_https_url(valid), valid)
        invalid = (
            "http://images.example/item.png",
            "https://user:secret@images.example/item.png",
            "https://images.example:8443/item.png",
            "https://127.0.0.1/item.png",
            "https://10.0.0.1/item.png",
            "https://[::1]/item.png",
            " https://images.example/item.png",
        )
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(WebImageError):
                validate_public_https_url(url)

    async def test_download_disables_automatic_redirects_and_validates_image(self):
        service = WebImageService(timeout_seconds=5)
        session = FakeSession([FakeResponse(body=make_png())])

        async def get_session():
            return session

        service._get_session = get_session
        image = await service.download("https://images.example/item.png")

        self.assertEqual(image.data, make_png())
        self.assertEqual(image.mime_type, "image/png")
        self.assertEqual((image.width, image.height), (16, 12))
        self.assertFalse(session.calls[0][1]["allow_redirects"])

    async def test_each_redirect_is_revalidated(self):
        service = WebImageService(timeout_seconds=5)
        session = FakeSession(
            [
                FakeResponse(status=302, location="https://127.0.0.1/private.png"),
            ]
        )

        async def get_session():
            return session

        service._get_session = get_session
        with self.assertRaises(WebImageError):
            await service.download("https://images.example/item.png")
        self.assertEqual(len(session.calls), 1)

    async def test_redirect_limit_is_enforced(self):
        service = WebImageService(timeout_seconds=5)
        session = FakeSession(
            [
                FakeResponse(status=302, location=f"/redirect/{index}")
                for index in range(MAX_REDIRECTS + 1)
            ]
        )

        async def get_session():
            return session

        service._get_session = get_session
        with self.assertRaises(WebImageError):
            await service.download("https://images.example/item.png")
        self.assertEqual(len(session.calls), MAX_REDIRECTS + 1)

    async def test_non_image_and_invalid_image_are_rejected(self):
        for response in (
            FakeResponse(body=b"not an image", content_type="text/html"),
            FakeResponse(body=b"not an image", content_type="image/png"),
        ):
            with self.subTest(content_type=response.headers["Content-Type"]):
                service = WebImageService(timeout_seconds=5)
                session = FakeSession([response])

                async def get_session():
                    return session

                service._get_session = get_session
                with self.assertRaises(WebImageError):
                    await service.download("https://images.example/item.png")

    async def test_total_timeout_is_an_error(self):
        service = WebImageService(timeout_seconds=1)

        async def slow_download(_url: str):
            await asyncio.sleep(2)

        service._download = slow_download
        with self.assertRaisesRegex(WebImageError, "timed out"):
            await service.download("https://images.example/item.png")


if __name__ == "__main__":
    unittest.main()
