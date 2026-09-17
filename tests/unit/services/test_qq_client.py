import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import botpy

from aiqq.interfaces.qq.client import AiQQClient, _incoming


class FakeRepository:
    def __init__(self):
        self.calls = []

    async def add_gateway_event(self, event_type, payload):
        self.calls.append((event_type, payload))
        return True


class FakeHandler:
    def __init__(self):
        self.messages = []

    async def handle(self, message):
        self.messages.append(message)


def payload(*, mentions):
    return {
        "id": "event-1",
        "d": {
            "id": "message-1",
            "group_openid": "group-1",
            "content": "hello",
            "mentions": mentions,
            "author": {"member_openid": "member-1"},
        },
    }


class QQClientTests(unittest.IsolatedAsyncioTestCase):
    def make_client(self):
        client = object.__new__(AiQQClient)
        repository = FakeRepository()
        handler = FakeHandler()
        client._components = SimpleNamespace(
            group_messages=repository,
            message_handler=handler,
        )
        client.api = object()
        client._message_tasks = set()
        return client, repository, handler

    def test_sdk_file_handler_is_disabled(self):
        qq_config = SimpleNamespace(http_timeout_seconds=30)
        web_config = SimpleNamespace()
        components = SimpleNamespace()

        with patch.object(botpy.Client, "__init__", return_value=None) as initialize:
            AiQQClient(
                qq_config=qq_config,
                web_config=web_config,
                component_factory=lambda _client: components,
            )

        self.assertFalse(initialize.call_args.kwargs["ext_handlers"])
        self.assertEqual(initialize.call_args.kwargs["timeout"], 30)

    async def test_non_mention_is_stored_without_dispatch(self):
        client, repository, handler = self.make_client()
        event = payload(mentions=[])

        await client._handle_raw_group_message("GROUP_MESSAGE_CREATE", event)

        self.assertEqual(repository.calls, [("GROUP_MESSAGE_CREATE", event)])
        self.assertEqual(handler.messages, [])

    async def test_explicit_mention_is_stored_then_dispatched(self):
        client, repository, handler = self.make_client()
        event = payload(mentions=[{"is_you": True, "username": "AiQQ"}])
        incoming = SimpleNamespace(message_id="message-1")
        with patch("aiqq.interfaces.qq.client.GroupMessage") as group_message:
            with patch("aiqq.interfaces.qq.client._incoming", return_value=incoming):
                await client._handle_raw_group_message(
                    "GROUP_MESSAGE_CREATE", event
                )

        await asyncio.gather(*client._message_tasks)
        self.assertEqual(repository.calls, [("GROUP_MESSAGE_CREATE", event)])
        self.assertEqual(handler.messages, [incoming])
        group_message.assert_called_once()

    async def test_slow_conversation_does_not_block_persisting_next_gateway_event(self):
        client, repository, handler = self.make_client()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow(_message):
            entered.set()
            await release.wait()

        handler.handle = slow
        event = payload(mentions=[{"is_you": True}])
        with patch("aiqq.interfaces.qq.client.GroupMessage"):
            await asyncio.wait_for(client._handle_raw_group_message("GROUP_AT_MESSAGE_CREATE", event), 1)
            await entered.wait()
            await client._handle_raw_group_message("GROUP_MESSAGE_CREATE", payload(mentions=[]))
            self.assertEqual(len(repository.calls), 2)
            release.set()
            await asyncio.gather(*client._message_tasks)

    def test_raw_nested_attachment_survives_sdk_projection_for_empty_request(self):
        message = SimpleNamespace(group_openid="g", id="m", content="", author=None)
        for raw in (
            {"msg_elements": [{"attachments": [{"content_type": "image/gif"}]}]},
            {"msg_elements": [{"image": {"content_type": "image/png", "url": "https://example.test/image.png"}}]},
        ):
            with self.subTest(raw=raw):
                self.assertTrue(_incoming(message, raw_data=raw).has_attachments)

    def test_private_chat_callback_is_not_registered(self):
        self.assertFalse(hasattr(AiQQClient, "on_c2c_message_create"))


if __name__ == "__main__":
    unittest.main()
