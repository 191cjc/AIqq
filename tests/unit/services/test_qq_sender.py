import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from aiqq.logic.models import BotSentGroupMessage
from aiqq.services.qq.sender import QQMessageSender


class FakeRepository:
    def __init__(self):
        self.owned = True
        self.saved = []
        self.recalled = []

    async def add_bot_message(self, **kwargs):
        self.saved.append(kwargs)
        return True

    async def is_owned_bot_message(self, **kwargs):
        return self.owned

    async def mark_recalled(self, **kwargs):
        self.recalled.append(kwargs)
        return True


class FakeHttp:
    def __init__(self):
        self.routes = []

    async def request(self, route, **kwargs):
        self.routes.append(route)
        return None


class FakeApi:
    def __init__(self):
        self._http = FakeHttp()
        self.sent = []

    async def post_group_message(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(
            id=f"bot-{len(self.sent)}",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )


class QQMessageSenderTests(unittest.IsolatedAsyncioTestCase):
    async def test_proactive_text_omits_reply_fields_and_is_recorded(self):
        api = FakeApi()
        repository = FakeRepository()
        sender = QQMessageSender(api, repository, bot_username="AiQQ")

        target = await sender.send_proactive_text(
            group_id="group-1",
            content="announcement",
            origin="console",
        )

        self.assertEqual(target.message_id, "bot-1")
        self.assertEqual(
            api.sent,
            [
                {
                    "group_openid": "group-1",
                    "msg_type": 0,
                    "content": "announcement",
                }
            ],
        )
        self.assertEqual(repository.saved[0]["source_message_id"], "")
        self.assertEqual(repository.saved[0]["payload"]["origin"], "console")
        self.assertNotIn("msg_seq", repository.saved[0]["payload"])

    async def test_progress_send_is_recorded_and_can_be_recalled(self):
        api = FakeApi()
        repository = FakeRepository()
        sender = QQMessageSender(api, repository, bot_username="AiQQ")

        target = await sender.send_text_reply(
            group_id="group-1",
            source_message_id="user-1",
            content="searching",
            msg_seq=1,
            progress=True,
        )
        recalled = await sender.recall_from_group(target)

        self.assertTrue(recalled)
        self.assertTrue(repository.saved[0]["progress"])
        self.assertEqual(api._http.routes[0].method, "DELETE")
        self.assertEqual(
            api._http.routes[0].url,
            "https://api.sgroup.qq.com/v2/groups/group-1/messages/bot-1",
        )
        self.assertEqual(repository.recalled[0]["message_id"], "bot-1")

    async def test_unowned_or_expired_target_never_reaches_qq_delete(self):
        api = FakeApi()
        repository = FakeRepository()
        sender = QQMessageSender(api, repository, bot_username="AiQQ")
        target = BotSentGroupMessage(
            "group-1", "not-owned", datetime.now(timezone.utc), True
        )
        repository.owned = False
        self.assertFalse(await sender.recall_from_group(target))

        expired = BotSentGroupMessage(
            "group-1",
            "old-bot",
            datetime.now(timezone.utc) - timedelta(minutes=3),
            True,
        )
        repository.owned = True
        self.assertFalse(await sender.recall_from_group(expired))
        self.assertEqual(api._http.routes, [])


if __name__ == "__main__":
    unittest.main()
