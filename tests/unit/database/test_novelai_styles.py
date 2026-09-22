import sqlite3
import tempfile
import unittest
from pathlib import Path

from aiqq.database import NovelAIStyleRepository, SQLiteConnection


class NovelAIStyleRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "state.db"
        self.connection = SQLiteConnection(self.path)
        self.repository = NovelAIStyleRepository(self.connection)
        await self.repository.initialize()

    async def asyncTearDown(self):
        await self.connection.close()
        self.directory.cleanup()

    async def test_style_survives_restart_and_repeated_initialization(self):
        prompt = "{{artist's style}},\n1.2::watercolor::,  soft lighting"
        await self.repository.save("member", f"  {prompt} \n")
        await self.connection.close()
        self.connection = SQLiteConnection(self.path)
        self.repository = NovelAIStyleRepository(self.connection)
        await self.repository.initialize()
        await self.repository.initialize()
        self.assertEqual(await self.repository.load("member"), prompt)

    async def test_replace_clear_and_sender_isolation(self):
        self.assertIsNone(await self.repository.load("first"))
        await self.repository.save("first", "watercolor")
        await self.repository.save("second'", "pixel art")
        await self.repository.save("first", "oil painting")
        self.assertEqual(await self.repository.load("first"), "oil painting")
        self.assertEqual(await self.repository.load("second'"), "pixel art")
        await self.repository.save("first", " \n\t ")
        await self.repository.save("first", "")
        self.assertIsNone(await self.repository.load("first"))
        self.assertEqual(await self.repository.load("second'"), "pixel art")

    async def test_empty_identity_cannot_read_or_modify_shared_settings(self):
        for member_openid in ("", " \t "):
            with self.subTest(member_openid=member_openid):
                with self.assertRaises(ValueError):
                    await self.repository.load(member_openid)
                with self.assertRaises(ValueError):
                    await self.repository.save(member_openid, "watercolor")

    async def test_failed_write_rolls_back_and_keeps_connection_usable(self):
        await self.repository.save("member", "watercolor")
        async with self.connection.transaction() as connection:
            await connection.execute(
                """
                CREATE TRIGGER reject_style_update AFTER UPDATE ON novelai_user_styles
                BEGIN SELECT RAISE(ABORT, 'test write failed'); END
                """
            )
        with self.assertRaises(sqlite3.IntegrityError):
            await self.repository.save("member", "oil painting")
        self.assertEqual(await self.repository.load("member"), "watercolor")
        await self.repository.save("other", "pixel art")
        self.assertEqual(await self.repository.load("other"), "pixel art")


if __name__ == "__main__":
    unittest.main()
