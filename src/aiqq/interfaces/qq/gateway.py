"""Gateway monitoring and raw full-group-message capture."""

from __future__ import annotations

import inspect
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from botpy.gateway import BotWebSocket


GROUP_MESSAGE_EVENTS = {"GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"}
RawGroupMessageCallback = Callable[..., Awaitable[None] | None]


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
        session: Any,
        connection: Any,
        *,
        on_connected: Callable[[bool], None],
        on_disconnected: Callable[[int | None, str], None],
        on_group_message: RawGroupMessageCallback | None = None,
    ) -> None:
        super().__init__(session, connection)
        self._on_gateway_connected = on_connected
        self._on_gateway_disconnected = on_disconnected
        self._on_group_message = on_group_message
        self._capture_connection_id = uuid.uuid4().hex
        self._raw_callback_parameters = (
            inspect.signature(on_group_message).parameters if on_group_message else {}
        )
        if on_group_message is not None:
            self._parser.setdefault("group_message_create", lambda _payload: None)

    async def on_message(self, ws: Any, message: str) -> None:
        payload: object = None
        event = ""
        try:
            payload = json.loads(message)
            if isinstance(payload, dict) and isinstance(payload.get("t"), str):
                event = payload["t"]
        except (json.JSONDecodeError, TypeError):
            pass

        if event in {"READY", "RESUMED"}:
            self._on_gateway_connected(event == "RESUMED")
        if (
            _is_group_event(event, payload)
            and isinstance(payload, dict)
            and self._on_group_message is not None
        ):
            # Preserve the original WebSocket text before SDK normalization.
            # Older callbacks remain supported without polluting the envelope.
            supports_kwargs = any(
                item.kind is inspect.Parameter.VAR_KEYWORD
                for item in self._raw_callback_parameters.values()
            )
            metadata = {"raw_text": message, "connection_id": self._capture_connection_id}
            kwargs = {
                name: value for name, value in metadata.items()
                if supports_kwargs or name in self._raw_callback_parameters
            }
            pending = self._on_group_message(event, payload, **kwargs)
            if inspect.isawaitable(pending):
                await pending
        await super().on_message(ws, message)

    async def on_closed(self, close_status_code: int | None, close_msg: object) -> None:
        self._on_gateway_disconnected(close_status_code, str(close_msg or ""))
        await super().on_closed(close_status_code, close_msg)

    async def on_error(self, exception: BaseException) -> None:
        self._on_gateway_disconnected(None, str(exception))
        await super().on_error(exception)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _is_group_event(event: str, payload: object) -> bool:
    if not event or not isinstance(payload, dict):
        return False
    data = payload.get("d")
    return event.startswith("GROUP_") or (
        isinstance(data, dict) and isinstance(data.get("group_openid"), str)
    )
