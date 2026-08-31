import tempfile
import unittest

from image_generation import ImageUsageStore


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
        self.assertIn("正在生成", decision.message)

        await store.finish_attempt("user:1")
        self.assertTrue((await store.reserve_attempt("user:2")).allowed)


if __name__ == "__main__":
    unittest.main()
