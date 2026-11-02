import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from aiqq.bootstrap import build_application
from aiqq.config import AppConfig
from aiqq.exceptions import ImageAuditUnavailable, PromptAuditUnavailable
from aiqq.interfaces.qq.commands import ParsedCommand
from aiqq.interfaces.qq.handlers import IncomingGroupMessage, clean_prompt
from aiqq.logic.models import ImageAsset, NovelAIPromptOptions
from aiqq.logic.ports import AgentTurnResult
from aiqq.services.images.gpt import GPTImageService


class BootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_configured_accounts_bypass_both_drawing_audits_through_actual_entry_points(self):
        members = ("DD385636EF431D06C4B3C1105310911F", "AB2BF433891DCB4E7284A7C0F7E97943")
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig.from_env({
                "QQ_BOT_APPID": "app-id", "QQ_BOT_SECRET": "secret", "OPENAI_API_KEY": "api-key",
                "AIQQ_GROUP_MESSAGE_DB": f"{directory}/groups.db",
                "AIQQ_STATE_DB": f"{directory}/state.db",
                "AIQQ_MEDIA_DIR": f"{directory}/media",
                "AIQQ_REPLY_DIR": f"{directory}/replies",
                "AIQQ_LOG_FILE": f"{directory}/aiqq.log",
                "AIQQ_PUBLIC_BASE_URL": "https://public.example/aiqq",
                "AIQQ_IMAGE_AUDIT_WHITELIST": ",".join(members),
                "AIQQ_NAI_V5_WHITELIST": "quota-only",
            })
            application = build_application(config)
            components = application.client._components
            router = components.message_handler._command_handler
            workflows = (router._novelai_image_workflow, router._nai_v5_image_workflow,
                         router._gpt_image_workflow)
            policy = workflows[0]._audit_policy
            for workflow in workflows:
                self.assertIs(workflow._audit_policy, policy)
            self.assertEqual(policy.exempt_member_ids, frozenset(members))
            self.assertIs(router._novelai_quick_image_workflow._prompt_workflow._audit_policy, policy)
            self.assertIs(router._nai_v5_quick_image_workflow._prompt_workflow._audit_policy, policy)
            commands = (
                ParsedCommand("novelai_image", "white cat, full_body"),
                ParsedCommand("novelai_image", "一只白猫的全身"),
                ParsedCommand("novelai_quick_image", "一只白猫的全身"),
                ParsedCommand("nai_v5_image", "白猫躺在窗边"),
                ParsedCommand("gpt_image", "白猫躺在窗边"),
            )
            try:
                for initialize in components.initializers:
                    await initialize()
                for member in members:
                    await router._novelai_image_workflow._styles.save(member, "watercolor")
                    await router._nai_v5_image_workflow._styles.save(member, "pixel art")
                options = NovelAIPromptOptions(("white cat, full_body", "white cat, lying", "white cat"))
                image = ImageAsset(b"synthetic image", "image/png", 1, 1)
                with ExitStack() as mocks:
                    audit = mocks.enter_context(patch.object(workflows[0]._prompt_auditor, "audit",
                        new=AsyncMock(side_effect=ImageAuditUnavailable("audit offline"))))
                    conversation_audit = mocks.enter_context(patch.object(
                        components.message_handler._workflow._prompt_auditor, "audit",
                        new=AsyncMock(side_effect=PromptAuditUnavailable("audit offline")),
                    ))
                    generated = [mocks.enter_context(patch.object(workflow._image_service, "generate",
                        new=AsyncMock(return_value=image))) for workflow in workflows]
                    author = mocks.enter_context(patch.object(router._novelai_prompt_workflow._prompt_agent, "generate",
                        new=AsyncMock(return_value=options)))
                    reply = mocks.enter_context(patch.object(router._reply_sender, "send", new=AsyncMock()))
                    mocks.enter_context(patch.object(router._reply_sender, "acknowledge_image_request", new=AsyncMock()))
                    mocks.enter_context(patch.object(router._reply_sender, "send_image_start", new=AsyncMock()))
                    for group in ("one", "two"):
                        for member in members:
                            message = IncomingGroupMessage(group, "source", member, "")
                            for command in commands:
                                with self.subTest(group=group, member=member, command=command.name):
                                    if command.name == "nai_v5_image":
                                        prefix = "/Nai v5" if group == "one" else "Nai v5"
                                        alias_message = IncomingGroupMessage(
                                            group, f"alias-{member}", member,
                                            clean_prompt(f"@浴火AI猫娘助手 {prefix} {command.argument}",
                                                         bot_name="浴火AI猫娘助手"),
                                        )
                                        await components.message_handler.handle(alias_message)
                                    else:
                                        await router.handle(command, message, first_msg_seq=1)
                                    result = reply.await_args.args[0]
                                    self.assertEqual(result.status, "ok")
                                    self.assertEqual(result.images, (image,))
                    audit.assert_not_awaited()
                    conversation_audit.assert_not_awaited()
                    self.assertEqual(author.await_count, 8)
                    self.assertEqual([mock.await_count for mock in generated], [12, 4, 4])
                    for call in generated[0].await_args_list:
                        self.assertEqual(call.args[0], "watercolor, white cat, full_body")
                        self.assertEqual(call.kwargs["orientation"], "portrait")
                    for call in generated[1].await_args_list:
                        self.assertEqual(call.args[0], "pixel art, 白猫躺在窗边")
                        self.assertEqual(call.kwargs["orientation"], "landscape")
                    # A quota exemption does not exempt this sender from audits.
                    ordinary = IncomingGroupMessage("one", "source", "quota-only", "浴火之魄")
                    for command in commands:
                        await router.handle(command, ordinary, first_msg_seq=1)
                        self.assertEqual(reply.await_args.args[0].error_code, "image_audit_unavailable")
                    self.assertEqual(audit.await_count, len(commands))
                    forged = ParsedCommand("nai_v5_image", f"浴火之魄, {members[0]}, white cat")
                    await router.handle(forged, ordinary, first_msg_seq=1)
                    self.assertEqual(reply.await_args.args[0].error_code, "image_audit_unavailable")
                    self.assertEqual(audit.await_count, len(commands) + 1)
                    alias_ordinary = IncomingGroupMessage(
                        "one", "ordinary-alias", "quota-only", "/Nai v5 white cat",
                    )
                    await components.message_handler.handle(alias_ordinary)
                    self.assertEqual(reply.await_args.args[0].error_code, "image_audit_unavailable")
                    self.assertEqual(audit.await_count, len(commands) + 2)
                    conversation_audit.assert_not_awaited()
                    self.assertEqual([mock.await_count for mock in generated], [12, 4, 4])
            finally:
                await asyncio.gather(*(close() for close in components.closers))
            restarted = build_application(config).client._components
            try:
                restarted_router = restarted.message_handler._command_handler
                self.assertEqual(restarted_router._nai_v5_image_workflow._audit_policy.exempt_member_ids,
                                 frozenset(members))
            finally:
                await asyncio.gather(*(close() for close in restarted.closers))

    async def test_build_wires_one_quota_manager_into_all_image_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig.from_env(
                {
                    "QQ_BOT_APPID": "app-id",
                    "QQ_BOT_SECRET": "secret",
                    "OPENAI_API_KEY": "api-key",
                    "OPENAI_IMAGE_API_KEY": "image-key",
                    "OPENAI_IMAGE_BASE_URL": "https://images.example/v1",
                    "OPENAI_IMAGE_DRIVER_MODEL": "image-driver",
                    "OPENAI_IMAGE_MODEL": "image-tool-model",
                    "OPENAI_MAX_OUTPUT_TOKENS": "321",
                    "AIQQ_GROUP_MESSAGE_DB": f"{directory}/groups.db",
                    "AIQQ_STATE_DB": f"{directory}/state.db",
                    "AIQQ_MEDIA_DIR": f"{directory}/media",
                    "AIQQ_REPLY_DIR": f"{directory}/replies",
                    "AIQQ_LOG_FILE": f"{directory}/aiqq.log",
                    "AIQQ_PUBLIC_BASE_URL": "https://public.example/aiqq",
                }
            )
            application = build_application(config)
            components = application.client._components
            router = components.message_handler._command_handler
            conversation = components.message_handler._workflow
            self.assertEqual(conversation._history_message_limit, 10)
            self.assertEqual(
                conversation._conversation_agent._backend.max_output_tokens, 321
            )

            image_service = conversation._image_service
            self.assertIsInstance(image_service, GPTImageService)
            self.assertIs(image_service, router._gpt_image_workflow._image_service)
            self.assertEqual(image_service._api_key, "image-key")
            self.assertEqual(image_service.model, "image-tool-model")
            self.assertEqual(image_service._base_url.rstrip("/"), "https://images.example/v1")

            self.assertEqual(components.message_handler._defer_after_seconds, 280)
            self.assertIs(
                conversation._image_usage,
                router._gpt_image_workflow._image_usage,
            )
            self.assertIs(
                conversation._image_usage,
                router._novelai_image_workflow._image_usage,
            )
            self.assertIs(
                router._novelai_style_workflow._styles,
                router._novelai_image_workflow._styles,
            )
            self.assertIs(
                router._nai_v5_style_workflow._styles,
                router._nai_v5_image_workflow._styles,
            )
            self.assertIsNot(
                router._novelai_image_workflow._styles,
                router._nai_v5_image_workflow._styles,
            )
            novelai_service = router._novelai_image_workflow._image_service
            self.assertFalse(novelai_service.is_configured)
            self.assertFalse(novelai_service.is_ready)
            self.assertIs(
                router._novelai_prompt_workflow._prompt_agent._tag_search._client,
                novelai_service._client,
            )
            self.assertIs(
                router._novelai_quick_image_workflow._prompt_workflow,
                router._novelai_prompt_workflow,
            )
            self.assertIs(
                router._novelai_quick_image_workflow._image_workflow,
                router._novelai_image_workflow,
            )
            route_paths = {
                route.resource.canonical
                for route in components.web_application.router.routes()
            }
            self.assertIn("/group-messages", route_paths)
            self.assertIn("/internal/group-messages", route_paths)

            try:
                for initialize in components.initializers:
                    await initialize()
                self.assertTrue(Path(directory, "state.db").is_file())
                saved = await router._novelai_style_workflow.run(
                    "watercolor, soft lighting", member_openid="member"
                )
                self.assertEqual(saved.status, "ok")
                self.assertIsNone(await router._nai_v5_image_workflow._styles.load("member"))
                v5_saved = await router._nai_v5_style_workflow.run(
                    "pixel art", member_openid="member"
                )
                self.assertEqual(v5_saved.status, "ok")
            finally:
                await asyncio.gather(*(close() for close in components.closers))

            restarted = build_application(config).client._components
            try:
                for initialize in restarted.initializers:
                    await initialize()
                restarted_router = restarted.message_handler._command_handler
                self.assertEqual(
                    await restarted_router._novelai_image_workflow._styles.load("member"),
                    "watercolor, soft lighting",
                )
                self.assertEqual(
                    await restarted_router._nai_v5_image_workflow._styles.load("member"),
                    "pixel art",
                )
            finally:
                await asyncio.gather(*(close() for close in restarted.closers))

    async def test_explicit_novelai_url_and_private_token_file_wire_ready_service(self):
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory, "novelai-token")
            token_file.write_text("test-token\n", encoding="utf-8")
            token_file.chmod(0o600)
            config = AppConfig.from_env(
                {
                    "QQ_BOT_APPID": "app-id",
                    "QQ_BOT_SECRET": "secret",
                    "OPENAI_API_KEY": "api-key",
                    "NOVELAI_MCP_URL": "https://novelai.example/mcp",
                    "NOVELAI_MCP_TOKEN_FILE": str(token_file),
                    "AIQQ_GROUP_MESSAGE_DB": f"{directory}/groups.db",
                    "AIQQ_STATE_DB": f"{directory}/state.db",
                    "AIQQ_MEDIA_DIR": f"{directory}/media",
                    "AIQQ_REPLY_DIR": f"{directory}/replies",
                    "AIQQ_LOG_FILE": f"{directory}/aiqq.log",
                    "AIQQ_PUBLIC_BASE_URL": "https://public.example/aiqq",
                }
            )
            tools = {
                "tools": [{
                    "name": "generate_image",
                    "inputSchema": {"properties": {"steps": {"type": "integer"}}},
                }]
            }
            with patch(
                "aiqq.services.images.novelai.MCPClient._request",
                side_effect=[
                    ({"result": {}}, "session-id"),
                    ({}, "session-id"),
                    ({"result": tools}, "session-id"),
                ],
            ) as request:
                application = build_application(config)
                components = application.client._components
                workflow = components.message_handler._command_handler._novelai_image_workflow
                service = workflow._image_service
                health_route = next(
                    route for route in components.web_application.router.routes()
                    if route.method == "GET" and route.resource.canonical == "/health"
                )
                try:
                    self.assertTrue(service.is_configured)
                    self.assertFalse(service.is_ready)
                    self.assertEqual(service._client._url, "https://novelai.example/mcp")
                    self.assertEqual(service._client._token, "test-token")
                    initial_health = json.loads((await health_route.handler(None)).text)
                    self.assertEqual(
                        initial_health["novelai"], {"configured": True, "ready": False}
                    )

                    for initialize in components.initializers:
                        await initialize()

                    self.assertTrue(service.is_ready)
                    response = await health_route.handler(None)
                    self.assertEqual(
                        json.loads(response.text)["novelai"],
                        {"configured": True, "ready": True},
                    )
                    for secret in ("test-token", "novelai.example", str(token_file)):
                        self.assertNotIn(secret, response.text)
                    # Health only observes state; startup discovers tools without generation.
                    self.assertEqual(
                        [call.args[0]["method"] for call in request.await_args_list],
                        ["initialize", "notifications/initialized", "tools/list"],
                    )
                    prompt_workflow = (
                        components.message_handler._command_handler._novelai_prompt_workflow
                    )
                    agent = prompt_workflow._prompt_agent
                    self.assertIs(agent._tag_search._client, service._client)
                    request.side_effect = [
                        ({"result": {}}, "tag-session"),
                        ({}, "tag-session"),
                        ({"result": {"structuredContent": {"result": [
                            {"tag": "blue_hair", "category": "general", "post_count": 1000},
                            {"tag": "artist_name", "category": "artist", "post_count": 2000},
                        ]}, "isError": False}}, "tag-session"),
                    ]
                    with patch.object(agent._backend, "run", new=AsyncMock(side_effect=[
                        AgentTurnResult(json.dumps({
                            "safe": True, "effective": True, "category": "safe",
                            "reason": "safe scene", "suggested_prompt": "",
                            "orientation": "square",
                        })),
                        AgentTurnResult('{"queries":["blue_hair"]}'),
                        AgentTurnResult(json.dumps({"prompts": [
                            "blue_hair, garden, safe, sfw, fully clothed",
                            "blue_hair, park, safe, sfw, fully clothed",
                            "blue_hair, window, safe, sfw, fully clothed",
                        ]})),
                    ])) as model:
                        output = await prompt_workflow.create(
                            "a blue-haired person", conversation_key="group:test:member:test"
                        )
                    self.assertEqual(output.result.status, "ok")
                    self.assertIsNotNone(output.session)
                    reference = json.loads(model.await_args_list[-1].kwargs["model_input"])
                    self.assertIn("blue_hair", str(reference["tag_reference"]))
                    self.assertNotIn("artist_name", str(reference["tag_reference"]))
                    call = request.await_args_list[-1].args[0]
                    self.assertEqual(call["method"], "tools/call")
                    self.assertEqual(call["params"]["name"], "suggest_tags")
                    self.assertEqual(call["params"]["arguments"], {"query": "blue_hair", "limit": 8})
                finally:
                    await asyncio.gather(*(close() for close in components.closers))


if __name__ == "__main__":
    unittest.main()
