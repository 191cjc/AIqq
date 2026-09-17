import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiqq.bootstrap import build_application
from aiqq.config import AppConfig
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
            novelai_service = router._novelai_image_workflow._image_service
            self.assertFalse(novelai_service.is_configured)
            self.assertFalse(novelai_service.is_ready)
            route_paths = {
                route.resource.canonical
                for route in components.web_application.router.routes()
            }
            self.assertIn("/group-messages", route_paths)
            self.assertIn("/internal/group-messages", route_paths)

            for initialize in components.initializers:
                await initialize()
            self.assertTrue(Path(directory, "state.db").is_file())
            await asyncio.gather(*(close() for close in components.closers))

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
                finally:
                    await asyncio.gather(*(close() for close in components.closers))


if __name__ == "__main__":
    unittest.main()
