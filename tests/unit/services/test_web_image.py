import asyncio
import unittest
from io import BytesIO
from unittest.mock import patch

import aiohttp
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
                with self.assertRaises(WebImageError) as caught:
                    await service.download("https://images.example/item.png")
                self.assertEqual(caught.exception.kind, "invalid_image")
                self.assertEqual(caught.exception.status_code, 200)

    async def test_total_timeout_is_an_error(self):
        service = WebImageService(timeout_seconds=1)

        async def slow_download(_url: str):
            await asyncio.sleep(2)

        service._download = slow_download
        with self.assertRaisesRegex(WebImageError, "timed out") as caught:
            await service.download("https://images.example/item.png")
        self.assertEqual(caught.exception.kind, "timeout")

    async def test_http_failures_preserve_kind_and_status_without_response_body(self):
        for status, kind in (
            (404, "not_found"), (410, "not_found"),
            (401, "access_denied"), (403, "access_denied"),
            (429, "download_failed"), (500, "download_failed"),
        ):
            with self.subTest(status=status):
                service = WebImageService()
                session = FakeSession([FakeResponse(status=status, body=b"private body")])
                with patch.object(service, "_get_session", return_value=session):
                    with self.assertRaises(WebImageError) as caught:
                        await service.download("https://images.example/item?token=secret")
                self.assertEqual(caught.exception.kind, kind)
                self.assertEqual(caught.exception.status_code, status)
                self.assertNotIn("private", str(caught.exception))
                self.assertNotIn("secret", str(caught.exception))
                self.assertEqual(len(session.calls), 1)

    async def test_client_timeouts_are_not_collapsed_into_oserror(self):
        for failure in (TimeoutError("private URL"), aiohttp.ServerTimeoutError("secret")):
            with self.subTest(failure=type(failure).__name__):
                service = WebImageService()
                session = FakeSession([])
                with patch.object(service, "_get_session", return_value=session), \
                     patch.object(session, "get", side_effect=failure):
                    with self.assertRaises(WebImageError) as caught:
                        await service.download("https://images.example/item.png")
                self.assertEqual(caught.exception.kind, "timeout")
                self.assertIsNone(caught.exception.status_code)
                self.assertEqual(str(caught.exception), "web image download timed out")

    async def test_stream_timeout_and_cancellation_keep_their_meaning(self):
        for failure in (aiohttp.SocketTimeoutError("secret"), asyncio.CancelledError()):
            with self.subTest(failure=type(failure).__name__):
                response = FakeResponse(body=make_png())

                async def failing_chunks(_size):
                    yield b"partial"
                    raise failure

                response.content.iter_chunked = failing_chunks
                service = WebImageService()
                with patch.object(service, "_get_session", return_value=FakeSession([response])):
                    if isinstance(failure, asyncio.CancelledError):
                        with self.assertRaises(asyncio.CancelledError):
                            await service.download("https://images.example/item.png")
                    else:
                        with self.assertRaises(WebImageError) as caught:
                            await service.download("https://images.example/item.png")
                        self.assertEqual(caught.exception.kind, "timeout")

    async def test_size_limits_cover_declared_size_and_actual_stream(self):
        image_bytes = make_png()
        for response in (
            FakeResponse(body=image_bytes, content_length=len(image_bytes) + 1),
            FakeResponse(body=image_bytes + b"x", content_length=0),
        ):
            service = WebImageService()
            with patch("aiqq.services.images.web.MAX_IMAGE_BYTES", len(image_bytes)), \
                 patch.object(service, "_get_session", return_value=FakeSession([response])):
                with self.assertRaises(WebImageError) as caught:
                    await service.download("https://images.example/item.png")
            self.assertEqual(caught.exception.kind, "too_large")

    async def test_connection_and_url_policy_failures_do_not_claim_expiry(self):
        service = WebImageService()
        with self.assertRaises(WebImageError) as caught:
            await service.download("https://127.0.0.1/expired.png")
        self.assertEqual(caught.exception.kind, "download_failed")
        session = FakeSession([])
        with patch.object(service, "_get_session", return_value=session), \
             patch.object(session, "get", side_effect=OSError("404 expired private URL")):
            with self.assertRaises(WebImageError) as caught:
                await service.download("https://images.example/item.png")
        self.assertEqual(caught.exception.kind, "download_failed")
        self.assertIsNone(caught.exception.status_code)

    def test_legacy_error_constructor_still_has_safe_default(self):
        error = WebImageError("legacy failure")
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(error.kind, "download_failed")
        self.assertIsNone(error.status_code)


if __name__ == "__main__":
    unittest.main()
