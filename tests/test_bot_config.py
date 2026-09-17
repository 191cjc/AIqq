import json
import os
import signal
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bot import AiQQBot, gateway_restart_timeout_seconds, qq_http_timeout_seconds
from gateway_monitor import GatewayStatus


class BotConfigTests(unittest.TestCase):
    def test_qq_http_timeout_defaults_to_30_seconds(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(qq_http_timeout_seconds(), 30)

    def test_qq_http_timeout_accepts_environment_override(self):
        with patch.dict(
            os.environ, {"AIQQ_QQ_HTTP_TIMEOUT_SECONDS": "45"}, clear=True
        ):
            self.assertEqual(qq_http_timeout_seconds(), 45)

    def test_qq_http_timeout_rejects_out_of_range_value(self):
        with patch.dict(
            os.environ, {"AIQQ_QQ_HTTP_TIMEOUT_SECONDS": "121"}, clear=True
        ):
            with self.assertRaises(SystemExit):
                qq_http_timeout_seconds()

    def test_gateway_restart_timeout_defaults_to_90_seconds(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(gateway_restart_timeout_seconds(), 90)

    def test_gateway_restart_timeout_accepts_environment_override(self):
        with patch.dict(
            os.environ,
            {"AIQQ_GATEWAY_RESTART_TIMEOUT_SECONDS": "120"},
            clear=True,
        ):
            self.assertEqual(gateway_restart_timeout_seconds(), 120)


class BotHealthTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_bot(gateway_status):
        return SimpleNamespace(
            gateway_status=gateway_status,
            ai=SimpleNamespace(
                is_configured=True,
                backend_name="codex_app_server",
                codex_runtime={"running": True, "active_turns": 0},
                image_output_enabled=True,
                web_search_enabled=True,
                image_search_enabled=True,
            ),
            gpt_image=SimpleNamespace(is_configured=True),
            novelai=SimpleNamespace(is_configured=True, is_ready=True),
            group_messages=SimpleNamespace(is_open=True),
            group_message_viewer=SimpleNamespace(is_configured=True),
        )

    async def test_health_is_ok_while_gateway_is_connected(self):
        gateway = GatewayStatus()
        gateway.mark_connected(resumed=False)

        response = await AiQQBot.health(self.make_bot(gateway), None)
        payload = json.loads(response.text)

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["qq_gateway"]["connected"])
        self.assertEqual(payload["ai_backend"], "codex_app_server")
        self.assertTrue(payload["codex_app_server"]["running"])
        self.assertTrue(payload["web_image_search_enabled"])
        self.assertTrue(payload["group_message_capture"]["enabled"])
        self.assertTrue(payload["group_message_capture"]["database_open"])
        self.assertTrue(payload["group_message_capture"]["viewer_configured"])

    async def test_health_is_degraded_while_gateway_is_disconnected(self):
        gateway = GatewayStatus()
        gateway.mark_connected(resumed=False)
        gateway.mark_disconnected(4009, "Session timed out")

        response = await AiQQBot.health(self.make_bot(gateway), None)
        payload = json.loads(response.text)

        self.assertEqual(response.status, 503)
        self.assertEqual(payload["status"], "degraded")
        self.assertFalse(payload["qq_gateway"]["connected"])
        self.assertEqual(payload["qq_gateway"]["last_close_code"], 4009)

    async def test_stuck_gateway_requests_systemd_restart(self):
        gateway = GatewayStatus()
        gateway.mark_disconnected(4009, "Session timed out")
        bot = SimpleNamespace(
            gateway_status=gateway,
            gateway_restart_timeout=0,
        )

        with patch("bot.os.kill") as kill:
            await AiQQBot._restart_if_gateway_stuck(bot)

        kill.assert_called_once_with(os.getpid(), signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
