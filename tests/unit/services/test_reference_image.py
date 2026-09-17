import asyncio
import unittest
from unittest.mock import AsyncMock

from aiqq.exceptions import ReferenceImageUnavailable
from aiqq.logic.models import ImageAsset
from aiqq.services.images.reference import GroupReferenceImageLoader
from aiqq.services.images.web import WebImageError


class FakeRepository:
    def __init__(self, urls):
        self.urls = urls
        self.calls = []

    async def list_image_urls(self, group_id, record_id):
        self.calls.append((group_id, record_id))
        return self.urls


class FakeDownloader:
    def __init__(self):
        self.calls = []
        self.image = ImageAsset(b"image", "image/jpeg", 1, 1)

    async def download(self, url):
        self.calls.append(url)
        if "expired" in url:
            raise RuntimeError("expired")
        return self.image


class GroupReferenceImageLoaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_group_scoped_record_and_falls_back_between_attachments(self):
        repository = FakeRepository(
            (
                "https://images.example/expired.jpg",
                "https://images.example/valid.jpg",
            )
        )
        downloader = FakeDownloader()
        loader = GroupReferenceImageLoader(
            repository=repository,
            downloader=downloader,
        )

        image = await loader.load("group-a", 17)

        self.assertIs(image, downloader.image)
        self.assertEqual(repository.calls, [("group-a", 17)])
        self.assertEqual(downloader.calls, list(repository.urls))

    async def test_unknown_failure_is_not_inferred_from_expired_filename(self):
        downloader = FakeDownloader()
        loader = GroupReferenceImageLoader(
            repository=FakeRepository(("https://images.example/expired.jpg",)),
            downloader=downloader,
        )
        with self.assertRaises(ReferenceImageUnavailable) as caught:
            await loader.load("group-a", 18)
        self.assertEqual(caught.exception.kind, "download_failed")

    async def test_missing_record_never_downloads(self):
        downloader = AsyncMock()
        loader = GroupReferenceImageLoader(repository=FakeRepository(()), downloader=downloader)
        with self.assertRaises(ReferenceImageUnavailable) as caught:
            await loader.load("group-a", 18)
        self.assertEqual(caught.exception.kind, "missing")
        downloader.download.assert_not_awaited()

    async def test_only_homogeneous_known_candidate_failures_are_specific(self):
        for web_kind, reference_kind, statuses in (
            ("not_found", "expired", (404, 410)),
            ("timeout", "timeout", (None, None)),
            ("access_denied", "access_denied", (403, 403)),
            ("invalid_image", "invalid_image", (200, 200)),
            ("too_large", "too_large", (200, 200)),
        ):
            with self.subTest(kind=web_kind):
                downloader = AsyncMock()
                downloader.download.side_effect = [
                    WebImageError("private URL secret", kind=web_kind, status_code=status)
                    for status in statuses
                ]
                loader = GroupReferenceImageLoader(
                    repository=FakeRepository(("https://img.example/a?secret=1", "https://img.example/b")),
                    downloader=downloader,
                )
                with self.assertLogs("aiqq.services.images.reference", level="INFO") as logs:
                    with self.assertRaises(ReferenceImageUnavailable) as caught:
                        await loader.load("private-group", 18)
                self.assertEqual(caught.exception.kind, reference_kind)
                self.assertEqual(caught.exception.status_code, statuses[0] if len(set(statuses)) == 1 else None)
                self.assertEqual(downloader.download.await_count, 2)
                self.assertNotIn("secret", "\n".join(logs.output))
                self.assertNotIn("private", "\n".join(logs.output))

    async def test_mixed_candidate_errors_are_generic(self):
        for other in (
            WebImageError("timeout", kind="timeout"),
            WebImageError("denied", kind="access_denied", status_code=403),
            WebImageError("404 expired", kind="unrecognized"),
            RuntimeError("404 expired secret"),
        ):
            with self.subTest(other=type(other).__name__):
                downloader = AsyncMock()
                downloader.download.side_effect = [
                    WebImageError("gone", kind="not_found", status_code=404), other,
                ]
                loader = GroupReferenceImageLoader(
                    repository=FakeRepository(("https://img.example/a", "https://img.example/b")),
                    downloader=downloader,
                )
                with self.assertRaises(ReferenceImageUnavailable) as caught:
                    await loader.load("g", 18)
                self.assertEqual(caught.exception.kind, "download_failed")

    async def test_known_failure_allows_later_success_but_cancellation_stops_candidates(self):
        image = ImageAsset(b"image", "image/jpeg", 1, 1)
        for first in (WebImageError("gone", kind="not_found", status_code=404), asyncio.CancelledError()):
            with self.subTest(first=type(first).__name__):
                downloader = AsyncMock()
                downloader.download.side_effect = [first, image]
                loader = GroupReferenceImageLoader(
                    repository=FakeRepository(("https://img.example/a", "https://img.example/b")),
                    downloader=downloader,
                )
                if isinstance(first, asyncio.CancelledError):
                    with self.assertRaises(asyncio.CancelledError):
                        await loader.load("g", 18)
                    self.assertEqual(downloader.download.await_count, 1)
                else:
                    self.assertIs(await loader.load("g", 18), image)
                    self.assertEqual(downloader.download.await_count, 2)


if __name__ == "__main__":
    unittest.main()
