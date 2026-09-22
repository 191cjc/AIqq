import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiqq.bootstrap import build_application
from aiqq.config import AppConfig
from aiqq.logic.ports import AgentTurnResult
from aiqq.services.images.codex_responses import CodexResponsesImageService


class BootstrapTests(unittest.IsolatedAsyncioTestCase):
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

            image_service = conversation._image_service
            self.assertIsInstance(image_service, CodexResponsesImageService)
            self.assertIs(image_service, router._gpt_image_workflow._image_service)
            self.assertEqual(image_service._api_key, "image-key")
            self.assertEqual(image_service.driver_model, "image-driver")
            self.assertEqual(image_service.model, "image-tool-model")
            self.assertEqual(image_service._base_url, "https://images.example/v1")

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
