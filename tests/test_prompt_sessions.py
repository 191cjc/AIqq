import tempfile
import unittest
from unittest.mock import patch

from prompt_sessions import PromptSessionStore


PROMPTS = (
    "white cat, morning sunlight, safe, sfw",
    "white cat, night city, safe, sfw",
    "white cat, close-up portrait, safe, sfw",
)


class PromptSessionStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = f"{self.temp_dir.name}/memory.db"
        self.store = PromptSessionStore(self.db_path)
        await self.store.initialize()

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_session_survives_store_recreation(self):
        session = await self.store.create("group:1:member:1", PROMPTS)

        reloaded = await PromptSessionStore(self.db_path).load(
            "group:1:member:1", session.token
        )

        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded.prompts, PROMPTS)

    async def test_session_is_bound_to_conversation(self):
        session = await self.store.create("group:1:member:1", PROMPTS)

        reloaded = await self.store.load("group:1:member:2", session.token)

        self.assertIsNone(reloaded)

    async def test_expired_session_is_rejected(self):
        with patch("prompt_sessions.time.time", return_value=1_000):
            session = await self.store.create("group:1:member:1", PROMPTS)

        with patch("prompt_sessions.time.time", return_value=1_901):
            reloaded = await self.store.load(
                "group:1:member:1", session.token
            )

        self.assertIsNone(reloaded)

    async def test_exactly_three_options_are_required(self):
        with self.assertRaises(ValueError):
            await self.store.create(
                "group:1:member:1", ("option one", "option two")
            )


if __name__ == "__main__":
    unittest.main()
