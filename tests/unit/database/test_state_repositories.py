import tempfile
import unittest
from pathlib import Path

from aiqq.database import (
    ImageUsageRepository,
    NovelAIPromptSessionRepository,
    SQLiteConnection,
)
from aiqq.logic.models import NovelAIPromptOptions


OPTIONS = NovelAIPromptOptions(
    (
        "white cat, daylight, safe, sfw",
        "white cat, night city, safe, sfw",
        "white cat, portrait, safe, sfw",
    )
)


class StateRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.connection = SQLiteConnection(
            Path(self.temporary_directory.name) / "state.db"
        )

    async def asyncTearDown(self):
        await self.connection.close()
        self.temporary_directory.cleanup()

    async def test_image_usage_preserves_and_increments_existing_rows(self):
        repository = ImageUsageRepository(self.connection)
        await repository.initialize()
        async with self.connection.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO image_generation_usage
                    (conversation_key, usage_date, success_count)
                VALUES (?, ?, ?)
                """,
                ("group:g:member:u", "2026-09-09", 4),
            )

        await repository.increment_success("group:g:member:u", "2026-09-09")

        self.assertEqual(
            await repository.success_count(
                "group:g:member:u", "2026-09-09"
            ),
            5,
        )
        self.assertEqual(
            await repository.success_count("missing", "2026-09-09"), 0
        )

    async def test_prompt_session_is_scoped_and_expires_after_fifteen_minutes(self):
        now = [1_000.0]
        repository = NovelAIPromptSessionRepository(
            self.connection,
            clock=lambda: now[0],
            token_factory=lambda: "stable-token",
        )
        await repository.initialize()
        created = await repository.create("group:g:member:u", OPTIONS)

        self.assertEqual(created.token, "stable-token")
        self.assertEqual(
            (await repository.load("group:g:member:u", "stable-token")).options,
            OPTIONS,
        )
        self.assertIsNone(
            await repository.load("group:g:member:other", "stable-token")
        )
        now[0] += 15 * 60
        self.assertIsNone(
            await repository.load("group:g:member:u", "stable-token")
        )


if __name__ == "__main__":
    unittest.main()
