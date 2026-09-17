import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

import botpy

from aiqq.interfaces.qq.client import AiQQClient, _incoming
from aiqq.interfaces.qq.handlers import GroupMessageHandler


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


def payload(*, mentions, content="hello"):
    return {
        "id": "event-1",
        "d": {
            "id": "message-1",
            "group_openid": "group-1",
            "content": content,
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
        client._bot_name = "AiQQ"
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

    async def test_text_mention_uses_runtime_name_and_dispatches_clean_content(self):
        client, repository, handler = self.make_client()
        client._bot_name = "CurrentBot"
        event = payload(mentions=[], content="  @CurrentBot你好")

        def make_group_message(_api, _event_id, data):
            return SimpleNamespace(
                group_openid=data["group_openid"],
                id=data["id"],
                content=data["content"],
                author=SimpleNamespace(member_openid="member-1"),
                attachments=[],
            )

        with patch(
            "aiqq.interfaces.qq.client.GroupMessage", side_effect=make_group_message
        ):
            await client._handle_raw_group_message("GROUP_MESSAGE_CREATE", event)

        await asyncio.gather(*client._message_tasks)
        self.assertEqual(repository.calls, [("GROUP_MESSAGE_CREATE", event)])
        self.assertEqual(len(handler.messages), 1)
        self.assertEqual(handler.messages[0].content, "你好")

    async def test_text_mention_rejects_old_partial_and_mid_message_names(self):
        for content in ("@OldBot 你好", "@Current 你好", "你好 @CurrentBot"):
            with self.subTest(content=content):
                client, repository, handler = self.make_client()
                client._bot_name = "CurrentBot"
                event = payload(mentions=[], content=content)

                await client._handle_raw_group_message(
                    "GROUP_MESSAGE_CREATE", event
                )

                self.assertEqual(
                    repository.calls, [("GROUP_MESSAGE_CREATE", event)]
                )
                self.assertEqual(handler.messages, [])

    async def test_ready_saves_runtime_bot_name_with_default_fallback(self):
        for reported_name, expected_name in (
            (" CurrentBot ", "CurrentBot"),
            ("", "AiQQ"),
        ):
            with self.subTest(reported_name=reported_name):
                sender = SimpleNamespace(set_bot_username=Mock())
                components = SimpleNamespace(
                    initializers=(),
                    sender=sender,
                    panel=SimpleNamespace(
                        sync=AsyncMock(
                            return_value=SimpleNamespace(
                                action="unchanged", panel_id="panel"
                            )
                        )
                    ),
                )
                with (
                    patch.object(botpy.Client, "__init__", return_value=None),
                    patch.object(
                        botpy.Client,
                        "robot",
                        new_callable=PropertyMock,
                        return_value=SimpleNamespace(name=reported_name),
                    ),
                ):
                    client = AiQQClient(
                        qq_config=SimpleNamespace(http_timeout_seconds=30),
                        web_config=SimpleNamespace(),
                        component_factory=lambda _client: components,
                    )
                    client._start_web_server = AsyncMock()
                    await client.on_ready()

                self.assertEqual(client._bot_name, expected_name)
                sender.set_bot_username.assert_called_once_with(expected_name)

    async def test_raw_and_sdk_delivery_of_same_message_is_processed_once(self):
        client, repository, _handler = self.make_client()
        sender = SimpleNamespace(send_text_reply=AsyncMock())
        workflow = SimpleNamespace(run=AsyncMock())
        client._components.message_handler = GroupMessageHandler(
            workflow=workflow,
            sender=sender,
            reply_sender=SimpleNamespace(),
            defer_after_seconds=1,
        )
        event = payload(mentions=[], content="@AiQQ")
        sdk_message = SimpleNamespace(
            group_openid="group-1",
            id="message-1",
            content="<@bot>",
            author=SimpleNamespace(member_openid="member-1"),
            attachments=[],
        )

        await asyncio.gather(
            client._handle_raw_group_message("GROUP_MESSAGE_CREATE", event),
            client.on_group_at_message_create(sdk_message),
        )
        await asyncio.gather(*client._message_tasks)

        self.assertEqual(repository.calls, [("GROUP_MESSAGE_CREATE", event)])
        sender.send_text_reply.assert_awaited_once()
        workflow.run.assert_not_awaited()

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
