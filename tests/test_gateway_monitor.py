import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from gateway_monitor import GatewayStatus, MonitoredBotWebSocket


class GatewayStatusTests(unittest.TestCase):
    def test_connect_disconnect_and_resume_are_reported(self):
        status = GatewayStatus()

        status.mark_connected(resumed=False)
        connected = status.snapshot()
        self.assertTrue(connected["connected"])
        self.assertTrue(connected["ever_connected"])
        self.assertEqual(connected["reconnect_count"], 0)

        status.mark_disconnected(4009, "Session timed out")
        disconnected = status.snapshot()
        self.assertFalse(disconnected["connected"])
        self.assertEqual(disconnected["disconnect_count"], 1)
        self.assertEqual(disconnected["last_close_code"], 4009)
        self.assertEqual(disconnected["last_close_message"], "Session timed out")

        status.mark_connected(resumed=True)
        resumed = status.snapshot()
        self.assertTrue(resumed["connected"])
        self.assertEqual(resumed["reconnect_count"], 1)
        self.assertEqual(resumed["disconnected_seconds"], 0)


class MonitoredBotWebSocketTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_connection():
        return SimpleNamespace(parser={})

    async def test_ready_and_resumed_events_update_status(self):
        status = GatewayStatus()
        socket = MonitoredBotWebSocket(
            {},
            self.make_connection(),
            on_connected=lambda resumed: status.mark_connected(resumed=resumed),
            on_disconnected=status.mark_disconnected,
        )

        with patch(
            "gateway_monitor.BotWebSocket.on_message", new=AsyncMock()
        ) as parent:
            await socket.on_message(
                None, json.dumps({"op": 0, "t": "READY", "s": 1})
            )
            self.assertTrue(status.connected)
            self.assertEqual(status.reconnect_count, 0)

            status.mark_disconnected(4009, "Session timed out")
            await socket.on_message(
                None, json.dumps({"op": 0, "t": "RESUMED", "s": 2})
            )
            self.assertEqual(status.reconnect_count, 1)
            self.assertEqual(parent.await_count, 2)

            status.mark_disconnected(9001, "Invalid session")
            await socket.on_message(
                None, json.dumps({"op": 0, "t": "READY", "s": 3})
            )
            self.assertEqual(status.reconnect_count, 2)
            self.assertEqual(parent.await_count, 3)

    async def test_close_event_records_gateway_failure(self):
        status = GatewayStatus()
        status.mark_connected(resumed=False)
        socket = MonitoredBotWebSocket(
            {},
            self.make_connection(),
            on_connected=lambda resumed: status.mark_connected(resumed=resumed),
            on_disconnected=status.mark_disconnected,
        )

        with patch(
            "gateway_monitor.BotWebSocket.on_closed", new=AsyncMock()
        ) as parent:
            await socket.on_closed(4009, "Session timed out")

        self.assertFalse(status.connected)
        self.assertEqual(status.last_close_code, 4009)
        parent.assert_awaited_once_with(4009, "Session timed out")

    async def test_group_events_are_forwarded_as_raw_payloads(self):
        received = []
        connection = self.make_connection()
        socket = MonitoredBotWebSocket(
            {},
            connection,
            on_connected=lambda _resumed: None,
            on_disconnected=lambda _code, _message: None,
            on_group_message=lambda event, payload: received.append(
                (event, payload)
            ),
        )
        payloads = [
            {"op": 0, "t": "GROUP_MESSAGE_CREATE", "s": 1, "d": {"id": "1"}},
            {"op": 0, "t": "GROUP_AT_MESSAGE_CREATE", "s": 2, "d": {"id": "2"}},
        ]

        with patch(
            "gateway_monitor.BotWebSocket.on_message", new=AsyncMock()
        ):
            for payload in payloads:
                await socket.on_message(None, json.dumps(payload))

        self.assertEqual(
            [event for event, _payload in received],
            ["GROUP_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE"],
        )
        self.assertEqual(received[0][1]["d"]["id"], "1")
        self.assertIn("group_message_create", connection.parser)


if __name__ == "__main__":
    unittest.main()
