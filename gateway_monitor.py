import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from botpy.gateway import BotWebSocket


GROUP_MESSAGE_EVENTS = {
    "GROUP_AT_MESSAGE_CREATE",
    "GROUP_MESSAGE_CREATE",
}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class GatewayStatus:
    connected: bool = False
    ever_connected: bool = False
    connected_since: str | None = None
    last_connected_at: str | None = None
    last_disconnected_at: str | None = None
    disconnect_count: int = 0
    reconnect_count: int = 0
    last_close_code: int | None = None
    last_close_message: str = ""
    _disconnected_monotonic: float | None = None

    def mark_connected(self, *, resumed: bool) -> None:
        now = _now_iso()
        was_connected = self.connected
        was_ever_connected = self.ever_connected
        self.connected = True
        self.ever_connected = True
        if not was_connected:
            self.connected_since = now
        self.last_connected_at = now
        self._disconnected_monotonic = None
        if not was_connected and (resumed or was_ever_connected):
            self.reconnect_count += 1

    def mark_disconnected(self, code: int | None, message: str) -> None:
        if self.connected or self._disconnected_monotonic is None:
            self.disconnect_count += 1
        self.connected = False
        self.connected_since = None
        self.last_disconnected_at = _now_iso()
        self.last_close_code = code
        self.last_close_message = " ".join(message.split())[:200]
        if self._disconnected_monotonic is None:
            self._disconnected_monotonic = time.monotonic()

    def snapshot(self) -> dict[str, object]:
        disconnected_seconds = 0
        if not self.connected and self._disconnected_monotonic is not None:
            disconnected_seconds = max(
                0, int(time.monotonic() - self._disconnected_monotonic)
            )
        return {
            "connected": self.connected,
            "ever_connected": self.ever_connected,
            "connected_since": self.connected_since,
            "last_connected_at": self.last_connected_at,
            "last_disconnected_at": self.last_disconnected_at,
            "disconnected_seconds": disconnected_seconds,
            "disconnect_count": self.disconnect_count,
            "reconnect_count": self.reconnect_count,
            "last_close_code": self.last_close_code,
            "last_close_message": self.last_close_message,
        }


class MonitoredBotWebSocket(BotWebSocket):
    def __init__(
        self,
        session,
        connection,
        *,
        on_connected: Callable[[bool], None],
        on_disconnected: Callable[[int | None, str], None],
        on_group_message: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        super().__init__(session, connection)
        self._on_gateway_connected = on_connected
        self._on_gateway_disconnected = on_disconnected
        self._on_group_message = on_group_message
        if on_group_message is not None:
            # qq-botpy 1.2.1 predates the full group-message event. The raw
            # callback above handles it, so keep the SDK from logging it as unknown.
            self._parser.setdefault("group_message_create", lambda _payload: None)

    async def on_message(self, ws, message):
        event = ""
        try:
            value = json.loads(message)
            if isinstance(value, dict) and isinstance(value.get("t"), str):
                event = value["t"]
        except (json.JSONDecodeError, TypeError):
            pass

        if event in {"READY", "RESUMED"}:
            self._on_gateway_connected(event == "RESUMED")
        if event in GROUP_MESSAGE_EVENTS and self._on_group_message is not None:
            self._on_group_message(event, value)
        await super().on_message(ws, message)

    async def on_closed(self, close_status_code, close_msg):
        self._on_gateway_disconnected(close_status_code, str(close_msg or ""))
        await super().on_closed(close_status_code, close_msg)

    async def on_error(self, exception: BaseException):
        self._on_gateway_disconnected(None, str(exception))
        await super().on_error(exception)
