import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from itertools import product
from types import SimpleNamespace
from unittest.mock import AsyncMock

from botpy.api import BotAPI
from botpy.errors import ServerError
from botpy.http import _handle_response

from aiqq.logic.models import BotSentGroupMessage
from aiqq.services.qq.sender import QQMessageSender


EXPIRED_REPLY_ERRORS = (
    (40034031, "msgid已经过期,不能回复"),
    (40034005, "回复消息msg_id已过期"),
)


class FakeRepository:
    def __init__(self):
        self.owned = True
        self.saved = []
        self.recalled = []
        self.attempts = []

    async def record_delivery_attempt(self, **kwargs):
        self.attempts.append(kwargs)
        return len(self.attempts)

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
    def __init__(self, outcomes=()):
        self._http = FakeHttp()
        self.sent = []
        self.uploads = []
        self.outcomes = list(outcomes)

    async def post_group_message(self, **kwargs):
        self.sent.append(kwargs)
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return SimpleNamespace(
            id=f"bot-{len(self.sent)}",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    async def post_group_file(self, **kwargs):
        self.uploads.append(kwargs)
        return {"file_info": "uploaded-file"}


class ExpiredReplyHttp:
    """Exercise the installed SDK's payload defaults and HTTP error parser."""

    def __init__(self, code, message):
        self.payloads = []
        self.uploads = []
        self.code = code
        self.message = message

    async def request(self, route, **kwargs):
        if route.path.endswith("/files"):
            self.uploads.append(kwargs["json"])
            return {"file_info": "uploaded-file"}
        self.payloads.append(kwargs["json"])
        if len(self.payloads) == 1:
            return await _handle_response(SimpleNamespace(
                status=400,
                request_info=SimpleNamespace(url=route.url),
                headers={"content-type": "application/json"},
                json=AsyncMock(return_value={
                    "message": self.message,
                    "code": self.code,
                    "err_code": self.code,
                }),
            ))
        return {"id": "proactive-1", "timestamp": "2026-09-15T09:14:14Z"}


class QQMessageSenderTests(unittest.IsolatedAsyncioTestCase):
    async def test_delivery_attempts_preserve_rejections_and_success_without_false_messages(self):
        repository = FakeRepository()
        api = FakeApi([ServerError("msgid已经过期,不能回复"), {"id": "actual", "extra_result": False}])
        sender = QQMessageSender(api, repository, bot_username="AiQQ")
        await sender.send_text_reply(
            group_id="g", source_message_id="source", content="answer", msg_seq=2,
            fallback_member_openid="member",
        )
        self.assertEqual([a["status"] for a in repository.attempts], ["started", "failed", "started", "succeeded"])
        self.assertEqual(repository.attempts[0]["parameters"]["request"], api.sent[0])
        self.assertEqual(repository.attempts[2]["parameters"]["request"], api.sent[1])
        self.assertEqual(repository.attempts[-1]["result"], {"id": "actual", "extra_result": False})
        self.assertEqual(len(repository.saved), 1)
        self.assertEqual(repository.saved[0]["message_id"], "actual")

    async def test_failed_upload_is_recorded_without_success_message(self):
        repository = FakeRepository()
        api = FakeApi()
        api.post_group_file = AsyncMock(side_effect=TimeoutError())
        sender = QQMessageSender(api, repository, bot_username="AiQQ")
        with self.assertRaises(TimeoutError):
            await sender.send_image_reply(
                group_id="g", source_message_id="source", public_url="https://example/image.png",
                mime_type="image/png", msg_seq=2,
            )
        self.assertEqual([a["status"] for a in repository.attempts], ["started", "failed"])
        self.assertEqual(repository.saved, [])
        self.assertEqual(api.sent, [])

    async def test_expired_replies_use_real_sdk_without_passive_context(self):
        for (code, message), message_type in product(EXPIRED_REPLY_ERRORS, (0, 2, 7)):
            with self.subTest(code=code, message_type=message_type):
                http = ExpiredReplyHttp(code, message)
                repository = FakeRepository()
                sender = QQMessageSender(BotAPI(http), repository, bot_username="AiQQ")
                keyboard = {"content": {"rows": []}}
                context = {
                    "group_id": "group-1", "source_message_id": "expired-source",
                    "msg_seq": 4, "fallback_member_openid": "member-1",
                }

                with self.assertLogs("aiqq.services.qq.sender", level="INFO") as logs:
                    if message_type == 2:
                        target = await sender.send_markdown_reply(
                            **context, content="<@member-1> answer",
                            record_content="complete answer", keyboard=keyboard,
                        )
                    elif message_type == 7:
                        target = await sender.send_image_reply(
                            **context, public_url="https://public.example/image.jpg",
                            mime_type="image/jpeg",
                        )
                    else:
                        target = await sender.send_text_reply(**context, content="answer")

                self.assertEqual(target.message_id, "proactive-1")
                self.assertEqual(len(http.payloads), 2)
                original, fallback = http.payloads
                self.assertEqual(original["msg_id"], "expired-source")
                self.assertEqual(original["msg_seq"], 4)
                self.assertIsNone(fallback["msg_id"])
                self.assertIsNone(fallback["msg_seq"])
                self.assertEqual(fallback["group_openid"], "group-1")
                self.assertEqual(fallback["msg_type"], message_type)
                self.assertEqual(len(repository.saved), 1)
                saved = repository.saved[0]
                self.assertEqual(saved["source_message_id"], "expired-source")
                self.assertEqual(saved["payload"]["delivery_mode"], "proactive")
                self.assertEqual(saved["payload"]["fallback_reason"], "expired_msg_id")
                self.assertEqual(saved["payload"]["source_message_id"], "expired-source")
                self.assertNotIn("msg_seq", saved["payload"])
                self.assertNotIn("msg_id", saved["payload"])
                if message_type == 2:
                    self.assertEqual(fallback["markdown"], {"content": "<@member-1> answer"})
                    self.assertEqual(fallback["keyboard"], keyboard)
                    self.assertEqual(saved["content"], "complete answer")
                elif message_type == 7:
                    self.assertEqual(len(http.uploads), 1)
                    self.assertFalse(http.uploads[0]["srv_send_msg"])
                    self.assertEqual(fallback["media"], original["media"])
                    self.assertEqual(fallback["media"], {"file_info": "uploaded-file"})
                    self.assertIsNone(original["content"])
                    self.assertIsNone(fallback["content"])
                    self.assertEqual(saved["content"], "[图片]")
                    self.assertEqual(saved["payload"]["content"], "[图片]")
                    self.assertEqual(
                        saved["payload"]["attachments"][0]["content_type"], "image/jpeg"
                    )
                else:
                    self.assertEqual(original["content"], "answer")
                    self.assertEqual(fallback["content"], "<@member-1> answer")
                    self.assertEqual(saved["content"], "answer")
                joined = " ".join(logs.output)
                self.assertIn("status=attempt", joined)
                self.assertIn("status=success", joined)
                for private_value in ("group-1", "member-1", "expired-source", "answer"):
                    self.assertNotIn(private_value, joined)

    async def test_non_expiry_errors_are_never_retried(self):
        errors = (
            ServerError("upstream unavailable"),
            ServerError("msgid已经过期,不能回复; unrelated details"),
            ServerError("回复消息msg_id已过期; unrelated details"),
            ServerError(" 回复消息msg_id已过期"),
            RuntimeError("msgid已经过期,不能回复"),
            RuntimeError("回复消息msg_id已过期"),
            TimeoutError("uncertain send outcome"),
            asyncio.CancelledError(),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__, message=str(error)):
                api = FakeApi([error])
                repository = FakeRepository()
                sender = QQMessageSender(api, repository, bot_username="AiQQ")
                with self.assertRaises(type(error)):
                    await sender.send_text_reply(
                        group_id="group", source_message_id="source", content="answer",
                        msg_seq=1, fallback_member_openid="member",
                    )
                self.assertEqual(len(api.sent), 1)
                self.assertEqual(repository.saved, [])

    async def test_expired_reply_requires_opt_in_source_and_non_progress(self):
        cases = (
            {"source_message_id": "source"},
            {"source_message_id": "", "fallback_member_openid": "member"},
            {"source_message_id": "source", "fallback_member_openid": "member", "progress": True},
        )
        for (code, message), kwargs in product(EXPIRED_REPLY_ERRORS, cases):
            with self.subTest(code=code, kwargs=kwargs):
                api = FakeApi([ServerError(message)])
                sender = QQMessageSender(api, FakeRepository(), bot_username="AiQQ")
                with self.assertRaises(ServerError):
                    await sender.send_text_reply(
                        group_id="group", content="answer", msg_seq=1, **kwargs,
                    )
                self.assertEqual(len(api.sent), 1)

    async def test_failed_proactive_retry_stops_without_recording_success(self):
        outcomes = (
            ServerError("msgid已经过期,不能回复"),
            ServerError("回复消息msg_id已过期"),
            ServerError("proactive quota exceeded"),
            TimeoutError("uncertain send outcome"),
            asyncio.CancelledError(),
            None,
        )
        for (code, message), outcome in product(EXPIRED_REPLY_ERRORS, outcomes):
            with self.subTest(code=code, outcome=outcome):
                api = FakeApi([ServerError(message), outcome])
                repository = FakeRepository()
                sender = QQMessageSender(api, repository, bot_username="AiQQ")
                error_type = type(outcome) if isinstance(outcome, BaseException) else RuntimeError
                with self.assertLogs("aiqq.services.qq.sender", level="INFO") as logs:
                    with self.assertRaises(error_type):
                        await sender.send_text_reply(
                            group_id="group", source_message_id="source", content="answer",
                            msg_seq=1, fallback_member_openid="member",
                        )
                self.assertEqual(len(api.sent), 2)
                self.assertEqual(repository.saved, [])
                self.assertNotIn("status=success", " ".join(logs.output))
                if not isinstance(outcome, asyncio.CancelledError):
                    self.assertIn("status=failed", " ".join(logs.output))

    async def test_successful_reply_stays_passive_with_opt_in(self):
        api = FakeApi()
        repository = FakeRepository()
        sender = QQMessageSender(api, repository, bot_username="AiQQ")
        await sender.send_text_reply(
            group_id="group", source_message_id="source", content="answer",
            msg_seq=4, fallback_member_openid="member",
        )
        self.assertEqual(len(api.sent), 1)
        self.assertEqual(api.sent[0]["msg_id"], "source")
        self.assertEqual(api.sent[0]["msg_seq"], 4)
        self.assertEqual(repository.saved[0]["payload"]["delivery_mode"], "reply")
        self.assertEqual(repository.saved[0]["payload"]["msg_seq"], 4)
        self.assertNotIn("fallback_reason", repository.saved[0]["payload"])

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
