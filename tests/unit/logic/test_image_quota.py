import asyncio
import unittest
from datetime import date

from aiqq.logic.image_quota import ImageQuotaManager


class FakeUsageRepository:
    def __init__(self, count=0):
        self.count = count
        self.incremented = []

    async def success_count(self, conversation_key, usage_date):
        return self.count

    async def increment_success(self, conversation_key, usage_date):
        self.incremented.append((conversation_key, usage_date))
        self.count += 1


class ImageQuotaManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_is_counted_for_the_reserved_user(self):
        repository = FakeUsageRepository()
        quota = ImageQuotaManager(
            repository, daily_limit=3, today=lambda: date(2026, 9, 9)
        )

        self.assertTrue((await quota.reserve_attempt("user-1")).allowed)
        await quota.record_success("user-1")
        await quota.finish_attempt("user-1")

        self.assertEqual(repository.incremented, [("user-1", "2026-09-09")])
        self.assertTrue((await quota.reserve_attempt("user-2")).allowed)

    async def test_global_slot_blocks_another_attempt_until_released(self):
        quota = ImageQuotaManager(FakeUsageRepository(), daily_limit=3)
        self.assertTrue((await quota.reserve_attempt("user-1")).allowed)

        denied = await quota.reserve_attempt("user-2")

        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "busy")
        await quota.finish_attempt("user-1")
        self.assertTrue((await quota.reserve_attempt("user-2")).allowed)

    async def test_daily_limit_denies_without_occupying_global_slot(self):
        quota = ImageQuotaManager(FakeUsageRepository(count=3), daily_limit=3)

        denied = await quota.reserve_attempt("user-1")

        self.assertEqual(denied.reason, "daily_limit")
        self.assertEqual(denied.daily_limit, 3)
        self.assertEqual(quota._active_conversation_key, None)


if __name__ == "__main__":
    unittest.main()
