import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiqq.database import ImageUsageRepository, SQLiteConnection
from aiqq.logic.image_quota import ImageQuotaManager
from aiqq.logic.models import ImagePromptAuditResult
from aiqq.logic.novelai import NovelAIImageWorkflow


class NaiV5QuotaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name, "state.db")
        self.connection = SQLiteConnection(self.path)
        self.repository = ImageUsageRepository(self.connection)
        await self.repository.initialize()
        self.current_day = date(2026, 10, 8)

    async def asyncTearDown(self):
        await self.connection.close()
        self.directory.cleanup()

    def quota(self, *, shared_limit=100, v5_limit=10, whitelist=frozenset()):
        return ImageQuotaManager(
            self.repository,
            daily_limit=shared_limit,
            nai_v5_daily_limit=v5_limit,
            nai_v5_whitelist=whitelist,
            today=lambda: self.current_day,
        )

    async def _generate_success(self, quota, *, group, member):
        key = f"group:{group}:member:{member}"
        decision = await quota.reserve_attempt(
            key, provider="nai_v5", member_openid=member
        )
        self.assertTrue(decision.allowed)
        try:
            await quota.record_success(
                key, provider="nai_v5", member_openid=member
            )
        finally:
            await quota.finish_attempt(key)

    async def test_ten_successes_across_groups_persist_and_reset_next_day(self):
        quota = self.quota()
        for index in range(10):
            await self._generate_success(
                quota, group="one" if index % 2 else "two", member="user"
            )
        day = "2026-10-08"
        self.assertEqual(await self.repository.nai_v5_success_count("user", day), 10)
        self.assertEqual(
            await self.repository.success_count("group:one:member:user", day), 5
        )
        denied = await quota.reserve_attempt(
            "group:three:member:user", provider="nai_v5", member_openid="user"
        )
        self.assertEqual((denied.reason, denied.daily_limit), ("daily_limit", 10))
        self.assertIsNone(quota._active_conversation_key)

        await self.connection.close()
        self.connection = SQLiteConnection(self.path)
        self.repository = ImageUsageRepository(self.connection)
        await self.repository.initialize()
        self.assertEqual(await self.repository.nai_v5_success_count("user", day), 10)

        self.current_day = date(2026, 10, 9)
        next_day_quota = self.quota()
        self.assertTrue((await next_day_quota.reserve_attempt(
            "group:three:member:user", provider="nai_v5", member_openid="user"
        )).allowed)
        await next_day_quota.finish_attempt("group:three:member:user")

    async def test_non_whitelisted_v5_still_obeys_shared_limit(self):
        quota = self.quota(shared_limit=2)
        for _ in range(2):
            await self._generate_success(quota, group="one", member="user")
        denied = await quota.reserve_attempt(
            "group:one:member:user", provider="nai_v5", member_openid="user"
        )
        self.assertEqual((denied.reason, denied.daily_limit), ("daily_limit", 2))
        self.assertEqual(
            await self.repository.nai_v5_success_count("user", "2026-10-08"), 2
        )

    async def test_whitelist_bypasses_both_daily_limits_but_not_busy_slot(self):
        day = "2026-10-08"
        key = "group:one:member:vip"
        async with self.connection.transaction() as connection:
            await connection.execute(
                "INSERT INTO image_generation_usage VALUES (?, ?, ?)",
                (key, day, 100),
            )
            await connection.execute(
                "INSERT INTO nai_v5_generation_usage VALUES (?, ?, ?)",
                ("vip", day, 10),
            )
        quota = self.quota(whitelist=frozenset({"vip"}))
        self.assertTrue((await quota.reserve_attempt(
            key, provider="nai_v5", member_openid="vip"
        )).allowed)
        busy = await quota.reserve_attempt("group:other:member:other")
        self.assertEqual(busy.reason, "busy")
        await quota.record_success(key, provider="nai_v5", member_openid="vip")
        await quota.finish_attempt(key)

        self.assertEqual(await self.repository.nai_v5_success_count("vip", day), 11)
        self.assertEqual(await self.repository.success_count(key, day), 100)
        self.assertEqual((await quota.reserve_attempt(key)).daily_limit, 100)
        self.assertTrue((await quota.reserve_attempt(
            "group:other:member:other"
        )).allowed)
        await quota.finish_attempt("group:other:member:other")

    async def test_v5_and_shared_counter_write_is_atomic(self):
        async with self.connection.transaction() as connection:
            await connection.execute("DROP TABLE image_generation_usage")
        with self.assertRaises(sqlite3.OperationalError):
            await self.repository.increment_nai_v5_success(
                "group:one:member:user", "user", "2026-10-08",
                count_toward_shared=True,
            )
        self.assertEqual(
            await self.repository.nai_v5_success_count("user", "2026-10-08"), 0
        )

    async def test_daily_limit_stops_workflow_before_generation(self):
        key = "group:one:member:user"
        async with self.connection.transaction() as connection:
            await connection.execute(
                "INSERT INTO nai_v5_generation_usage VALUES (?, ?, ?)",
                ("user", "2026-10-08", 10),
            )
        service = SimpleNamespace(generate=AsyncMock())
        on_image_start = AsyncMock()
        workflow = NovelAIImageWorkflow(
            prompt_auditor=SimpleNamespace(audit=AsyncMock(return_value=
                ImagePromptAuditResult(True, True, "safe", "accepted", "", "square")
            )),
            image_service=service,
            image_usage=self.quota(),
            styles=SimpleNamespace(load=AsyncMock(return_value=None)),
            provider_name="Nai v5",
            provider_id="nai_v5",
            quota_provider="nai_v5",
        )

        result = await workflow.run(
            "white cat", conversation_key=key, member_openid="user",
            on_image_start=on_image_start,
        )

        self.assertEqual(result.status, "rejected")
        self.assertIn("每日 10 张", result.full_text)
        on_image_start.assert_not_awaited()
        service.generate.assert_not_awaited()

    async def test_failed_workflow_releases_slot_without_charging_v5(self):
        quota = self.quota()
        service = SimpleNamespace(generate=AsyncMock(side_effect=RuntimeError("failed")))
        workflow = NovelAIImageWorkflow(
            prompt_auditor=SimpleNamespace(audit=AsyncMock(return_value=
                ImagePromptAuditResult(True, True, "safe", "accepted", "", "square")
            )),
            image_service=service,
            image_usage=quota,
            styles=SimpleNamespace(load=AsyncMock(return_value=None)),
            provider_name="Nai v5",
            provider_id="nai_v5",
            quota_provider="nai_v5",
        )
        key = "group:one:member:user"
        with self.assertLogs("aiqq.logic.novelai"):
            result = await workflow.run(
                "white cat", conversation_key=key, member_openid="user"
            )
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(
            await self.repository.nai_v5_success_count("user", "2026-10-08"), 0
        )
        self.assertTrue((await quota.reserve_attempt(
            key, provider="nai_v5", member_openid="user"
        )).allowed)
        await quota.finish_attempt(key)


if __name__ == "__main__":
    unittest.main()
