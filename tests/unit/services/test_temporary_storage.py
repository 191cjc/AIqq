import os
import tempfile
import time
import unittest
from pathlib import Path

from aiqq.logic.models import ImageAsset
from aiqq.services.storage import TemporaryMediaStore, TemporaryReplyStore


class TemporaryStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    async def asyncTearDown(self):
        self.temporary.cleanup()

    async def test_media_is_private_and_path_traversal_is_rejected(self):
        store = TemporaryMediaStore(
            self.root / "media", "https://public.example/aiqq", 60
        )
        await store.initialize()
        exported = await store.save(ImageAsset(b"jpeg", "image/jpeg", 1, 1))

        path = await store.resolve(exported.file_name)

        self.assertIsNotNone(path)
        self.assertEqual(path.read_bytes(), b"jpeg")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(exported.public_url.endswith(f"/media/{exported.file_name}"))
        self.assertIsNone(await store.resolve("../secret.jpg"))

    async def test_reply_round_trip_and_expiration(self):
        store = TemporaryReplyStore(
            self.root / "replies", "https://public.example/aiqq", 1
        )
        await store.initialize()
        exported = await store.save("# complete answer")
        path = store.directory / exported.file_name

        self.assertEqual(await store.load(exported.file_name), "# complete answer")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        old = time.time() - 10
        os.utime(path, (old, old))
        self.assertIsNone(await store.load(exported.file_name))
        self.assertFalse(path.exists())

    async def test_pending_reply_is_updated_in_place_and_completed(self):
        store = TemporaryReplyStore(
            self.root / "replies", "https://public.example/aiqq", 60
        )
        await store.initialize()
        exported = await store.create_pending("任务仍在处理中。")
        path = store.directory / exported.file_name

        pending = await store.load_document(exported.file_name)
        self.assertEqual(pending.content, "任务仍在处理中。")
        self.assertTrue(pending.pending)

        self.assertTrue(
            await store.update(exported.file_name, "最新进展。", pending=True)
        )
        self.assertEqual(path.read_text(encoding="utf-8"), "最新进展。")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertTrue((path.parent / f"{path.name}.pending").is_file())

        self.assertTrue(
            await store.update(exported.file_name, "# 最终回答", pending=False)
        )
        completed = await store.load_document(exported.file_name)
        self.assertEqual(completed.content, "# 最终回答")
        self.assertFalse(completed.pending)
        self.assertFalse((path.parent / f"{path.name}.pending").exists())

    async def test_pending_reply_rejects_invalid_or_expired_targets(self):
        store = TemporaryReplyStore(
            self.root / "replies", "https://public.example/aiqq", 1
        )
        await store.initialize()
        exported = await store.create_pending("处理中。")
        path = store.directory / exported.file_name
        old = time.time() - 10
        os.utime(path, (old, old))

        self.assertFalse(
            await store.update(exported.file_name, "过期结果。", pending=False)
        )
        self.assertFalse(await store.update("../secret.txt", "结果。", pending=False))
        self.assertFalse(path.exists())
        self.assertFalse((path.parent / f"{path.name}.pending").exists())


if __name__ == "__main__":
    unittest.main()
