import asyncio
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock, PropertyMock, patch

import botpy
from botpy.message import GroupMessage

from aiqq.interfaces.qq.client import AiQQClient, _incoming
from aiqq.interfaces.qq.command_router import ExplicitCommandRouter
from aiqq.interfaces.qq.handlers import GroupMessageHandler
from aiqq.logic.models import ConversationResult


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

    def make_routing_client(self):
        client, repository, _handler = self.make_client()
        client._bot_name = "CurrentBot"
        result = ConversationResult("ok", "complete", "complete")
        conversation = SimpleNamespace(run=AsyncMock(return_value=result))
        novelai = SimpleNamespace(run=AsyncMock(return_value=result))
        gpt = SimpleNamespace(run=AsyncMock(return_value=result))
        replies = SimpleNamespace(send=AsyncMock())
        sender = SimpleNamespace(send_text_reply=AsyncMock())
        router = ExplicitCommandRouter(
            reply_sender=replies,
            gpt_image_workflow=gpt,
            novelai_image_workflow=novelai,
            novelai_quick_image_workflow=SimpleNamespace(
                run=AsyncMock(return_value=result)
            ),
            novelai_prompt_workflow=Mock(),
            novelai_style_workflow=Mock(),
        )
        client._components.message_handler = GroupMessageHandler(
            workflow=conversation,
            sender=sender,
            reply_sender=replies,
            defer_after_seconds=1,
            command_handler=router,
        )
        return client, repository, conversation, novelai, gpt, replies, sender

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
        for content in (
            "@OldBot 你好", "@Current 你好", "你好 @CurrentBot",
            "[@OldBot] 你好", "[@Current] 你好", "你好 [@CurrentBot]",
            "[@CurrentBotExtra] 你好", "[@currentbot] 你好",
        ):
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

    async def test_native_mentions_route_to_novelai_once_in_each_callback_order(self):
        prompt = "white cat,\n  blue eyes, [@someone], safe, sfw"
        for prefix in ("<@bot-id>", "<@!bot-id>", "[@CurrentBot]", "@CurrentBot"):
            for order in ("sdk_first", "raw_first", "concurrent"):
                with self.subTest(prefix=prefix, order=order):
                    client, repository, conversation, novelai, gpt, replies, sender = self.make_routing_client()
                    event = payload(
                        mentions=[{"is_you": True, "username": "CurrentBot"}],
                        content=f" \t{prefix}\n /NovelAI生图 {prompt}  ",
                    )
                    event["d"]["unknown"] = {"nested": [None, False, 0, ""]}
                    original = deepcopy(event)
                    sdk_message = GroupMessage(client.api, event["id"], event["d"])
                    # The real SDK loses these fields; SDK-first must still work.
                    self.assertFalse(hasattr(sdk_message.mentions[0], "is_you"))
                    self.assertFalse(hasattr(sdk_message.mentions[0], "username"))

                    async def raw_delivery():
                        await client._handle_raw_group_message("GROUP_MESSAGE_CREATE", event)
                        await asyncio.gather(*client._message_tasks)

                    if order == "sdk_first":
                        await client.on_group_at_message_create(sdk_message)
                        await raw_delivery()
                    elif order == "raw_first":
                        await raw_delivery()
                        await client.on_group_at_message_create(sdk_message)
                    else:
                        await asyncio.gather(
                            raw_delivery(), client.on_group_at_message_create(sdk_message)
                        )

                    self.assertEqual(event, original)
                    self.assertEqual(repository.calls, [("GROUP_MESSAGE_CREATE", original)])
                    novelai.run.assert_awaited_once_with(
                        prompt,
                        conversation_key="group:group-1:member:member-1",
                        member_openid="member-1",
                        on_image_start=ANY,
                    )
                    self.assertTrue(callable(novelai.run.await_args.kwargs["on_image_start"]))
                    conversation.run.assert_not_awaited()
                    gpt.run.assert_not_awaited()
                    replies.send.assert_awaited_once()
                    sender.send_text_reply.assert_not_awaited()

    async def test_raw_bracketed_text_mention_without_metadata_routes_to_novelai(self):
        client, repository, conversation, novelai, gpt, _replies, _sender = self.make_routing_client()
        event = payload(mentions=[], content="  [@CurrentBot]/NovelAI生图 cat")

        await client._handle_raw_group_message("GROUP_MESSAGE_CREATE", event)
        await asyncio.gather(*client._message_tasks)

        self.assertEqual(repository.calls, [("GROUP_MESSAGE_CREATE", event)])
        novelai.run.assert_awaited_once_with(
            "cat", conversation_key="group:group-1:member:member-1", member_openid="member-1",
            on_image_start=ANY,
        )
        self.assertTrue(callable(novelai.run.await_args.kwargs["on_image_start"]))
        conversation.run.assert_not_awaited()
        gpt.run.assert_not_awaited()

    async def test_ingress_and_handler_preserve_second_mention_as_body(self):
        for callback in ("raw", "sdk"):
            for prefix in ("<@bot-id>", "[@CurrentBot]", "@CurrentBot"):
                for body in ("<@person> /NovelAI生图 cat", "[@CurrentBot] hello", "@CurrentBot hello"):
                    with self.subTest(callback=callback, prefix=prefix, body=body):
                        client, _repository, conversation, novelai, gpt, _replies, _sender = self.make_routing_client()
                        event = payload(
                            mentions=[{"is_you": True, "username": "CurrentBot"}],
                            content=f"{prefix} {body}",
                        )
                        if callback == "raw":
                            await client._handle_raw_group_message("GROUP_MESSAGE_CREATE", event)
                            await asyncio.gather(*client._message_tasks)
                        else:
                            await client.on_group_at_message_create(
                                GroupMessage(client.api, event["id"], event["d"])
                            )

                        conversation.run.assert_awaited_once()
                        self.assertEqual(conversation.run.await_args.kwargs["user_input"], body)
                        novelai.run.assert_not_awaited()
                        gpt.run.assert_not_awaited()

    async def test_native_bracket_image_only_uses_attachment_flow(self):
        for callback in ("raw", "sdk"):
            with self.subTest(callback=callback):
                client, repository, conversation, novelai, gpt, _replies, sender = self.make_routing_client()
                event = payload(
                    mentions=[{"is_you": True, "username": "CurrentBot"}],
                    content=" \t[@CurrentBot]\n ",
                )
                attachment = {"content_type": "image/gif", "url": "https://example.test/sticker.gif"}
                if callback == "raw":
                    event["d"]["msg_elements"] = [{"image": attachment}]
                    original = deepcopy(event)
                    await client._handle_raw_group_message("GROUP_MESSAGE_CREATE", event)
                    await asyncio.gather(*client._message_tasks)
                    self.assertEqual(repository.calls, [("GROUP_MESSAGE_CREATE", original)])
                else:
                    event["d"]["attachments"] = [attachment]
                    await client.on_group_at_message_create(
                        GroupMessage(client.api, event["id"], event["d"])
                    )

                conversation.run.assert_awaited_once()
                self.assertIn("图片或表情", conversation.run.await_args.kwargs["user_input"])
                novelai.run.assert_not_awaited()
                gpt.run.assert_not_awaited()
                sender.send_text_reply.assert_not_awaited()

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
