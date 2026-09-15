import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiqq.interfaces.qq.gateway import GatewayStatus, MonitoredBotWebSocket


class QQGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_raw_group_callback_completes_before_sdk_dispatch(self):
        events = []

        async def capture(event, payload):
            events.append((event, payload["d"]["id"]))

        connection = SimpleNamespace(parser={})
        socket = MonitoredBotWebSocket(
            {},
            connection,
            on_connected=lambda _resumed: None,
            on_disconnected=lambda _code, _message: None,
            on_group_message=capture,
        )
        with patch(
            "aiqq.interfaces.qq.gateway.BotWebSocket.on_message", new=AsyncMock()
        ) as parent:
            await socket.on_message(
                None,
                json.dumps(
                    {"op": 0, "t": "GROUP_MESSAGE_CREATE", "d": {"id": "m1"}}
                ),
            )
        self.assertEqual(events, [("GROUP_MESSAGE_CREATE", "m1")])
        parent.assert_awaited_once()
        self.assertIn("group_message_create", connection.parser)

    def test_gateway_status_reports_disconnect_and_reconnect(self):
        status = GatewayStatus()
        status.mark_connected(resumed=False)
        status.mark_disconnected(4009, " session   expired ")
        status.mark_connected(resumed=True)
        snapshot = status.snapshot()
        self.assertTrue(snapshot["connected"])
        self.assertEqual(snapshot["disconnect_count"], 1)
        self.assertEqual(snapshot["reconnect_count"], 1)
        self.assertEqual(snapshot["last_close_message"], "session expired")


if __name__ == "__main__":
    unittest.main()
