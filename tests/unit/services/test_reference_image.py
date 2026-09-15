import unittest

from aiqq.logic.models import ImageAsset
from aiqq.services.images.reference import GroupReferenceImageLoader


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

    async def test_missing_or_unusable_attachments_return_none(self):
        downloader = FakeDownloader()
        loader = GroupReferenceImageLoader(
            repository=FakeRepository(("https://images.example/expired.jpg",)),
            downloader=downloader,
        )
        self.assertIsNone(await loader.load("group-a", 18))


if __name__ == "__main__":
    unittest.main()
