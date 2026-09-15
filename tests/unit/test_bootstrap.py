import asyncio
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
