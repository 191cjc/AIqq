"""Operational health endpoint."""

from __future__ import annotations

from collections.abc import Callable

from aiohttp import web


class HealthHandler:
    def __init__(
        self,
        *,
        gateway_snapshot: Callable[[], dict[str, object]],
        database_is_open: Callable[[], bool],
        ai_backend: str,
    ) -> None:
        self._gateway_snapshot = gateway_snapshot
        self._database_is_open = database_is_open
        self._ai_backend = ai_backend

    async def serve(self, _request: web.Request) -> web.Response:
        gateway = self._gateway_snapshot()
        healthy = bool(gateway.get("connected")) and self._database_is_open()
        return web.json_response(
            {
                "status": "ok" if healthy else "degraded",
                "service": "AiQQ",
                "qq_gateway": gateway,
                "group_message_database_open": self._database_is_open(),
                "ai_backend": self._ai_backend,
                "conversation_context": "group_history_reference",
                "shared_ai_thread": False,
                "private_chat_enabled": False,
            },
            status=200 if healthy else 503,
        )
