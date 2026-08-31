import tempfile
import unittest

from media_store import TemporaryMediaStore
from novelai_service import GeneratedImage


class MediaStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = TemporaryMediaStore(
            self.temp_dir.name,
            "https://example.com/aiqq",
            ttl_seconds=900,
        )
        await self.store.initialize()

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    def test_image_is_saved_under_random_public_url(self):
        asset = self.store.save(GeneratedImage(b"image-data", "image/png"))

        self.assertTrue(asset.file_name.endswith(".png"))
        self.assertEqual(
            asset.public_url,
            f"https://example.com/aiqq/media/{asset.file_name}",
        )
        with open(f"{self.temp_dir.name}/{asset.file_name}", "rb") as image_file:
            self.assertEqual(image_file.read(), b"image-data")


if __name__ == "__main__":
    unittest.main()
