import os
import tempfile
import unittest
from unittest.mock import patch

from image_generation import (
    ImageUsageStore,
    RecentImageRequestStore,
)


class ImageUsageStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_successful_generations_count_toward_daily_limit(self):
        store = ImageUsageStore(
            f"{self.temp_dir.name}/usage.db",
            daily_limit=1,
        )
        await store.initialize()

        self.assertTrue((await store.reserve_attempt("user:1")).allowed)
        await store.record_success("user:1")
        await store.finish_attempt("user:1")

        decision = await store.reserve_attempt("user:1")
        self.assertFalse(decision.allowed)
        self.assertIn("额度", decision.message)

    async def test_only_one_generation_can_run_globally(self):
        store = ImageUsageStore(
            f"{self.temp_dir.name}/usage.db",
            daily_limit=100,
        )
        await store.initialize()

        self.assertTrue((await store.reserve_attempt("user:1")).allowed)
        decision = await store.reserve_attempt("user:2")

        self.assertFalse(decision.allowed)
        self.assertIn("上一张还在画", decision.message)
        self.assertIn("主人", decision.message)
        self.assertIn("喵", decision.message)

        await store.finish_attempt("user:1")
        self.assertTrue((await store.reserve_attempt("user:2")).allowed)

    def test_generic_daily_limit_takes_precedence_over_legacy_setting(self):
        with patch.dict(
            os.environ,
            {
                "AIQQ_IMAGE_USER_DAILY_LIMIT": "12",
                "AIQQ_NOVELAI_USER_DAILY_LIMIT": "7",
            },
            clear=True,
        ):
            store = ImageUsageStore.from_env()

        self.assertEqual(store.daily_limit, 12)

    def test_recent_image_context_is_scoped_and_expires(self):
        now = [100.0]
        store = RecentImageRequestStore(30, clock=lambda: now[0])

        store.mark("group:1:member:1")

        self.assertTrue(store.has_context("group:1:member:1"))
        self.assertFalse(store.has_context("group:1:member:2"))
        now[0] += 30
        self.assertFalse(store.has_context("group:1:member:1"))

    def test_recent_image_context_can_be_cleared(self):
        store = RecentImageRequestStore()
        store.mark("group:1:member:1")

        store.clear()

        self.assertFalse(store.has_context("group:1:member:1"))

if __name__ == "__main__":
    unittest.main()
