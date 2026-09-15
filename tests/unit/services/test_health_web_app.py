import json
import unittest

from aiohttp import web

from aiqq.interfaces.web.app import create_web_application
from aiqq.interfaces.web.health import HealthHandler


class StubHandler:
    async def serve(self, _request):
        return web.Response(text="ok")

    async def send_group_message(self, _request):
        return web.Response(text="ok")

    async def send_message(self, _request):
        return web.Response(text="ok")


class HealthAndWebApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_contract_reports_stateless_group_context(self):
        connected = HealthHandler(
            gateway_snapshot=lambda: {"connected": True},
            database_is_open=lambda: True,
            ai_backend="codex_sdk",
        )
        response = await connected.serve(None)
        payload = json.loads(response.text)

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["conversation_context"], "group_history_reference")
        self.assertFalse(payload["shared_ai_thread"])
        self.assertFalse(payload["private_chat_enabled"])

        degraded = HealthHandler(
            gateway_snapshot=lambda: {"connected": False},
            database_is_open=lambda: True,
            ai_backend="responses",
        )
        self.assertEqual((await degraded.serve(None)).status, 503)

    async def test_complete_web_application_registers_all_operational_routes(self):
        handler = StubHandler()
        application = create_web_application(
            health=handler,
            admin_messages=handler,
            media=handler,
            replies=handler,
            group_messages=handler,
        )

        routes = {
            (route.method, route.resource.canonical)
            for route in application.router.routes()
        }
        self.assertIn(("GET", "/health"), routes)
        self.assertIn(("POST", "/internal/group-messages"), routes)
        self.assertIn(("GET", "/group-messages"), routes)
        self.assertIn(("POST", "/group-messages"), routes)
        self.assertIn(("GET", "/media/{file_name}"), routes)
        self.assertIn(("GET", "/reply/{file_name}"), routes)


if __name__ == "__main__":
    unittest.main()
