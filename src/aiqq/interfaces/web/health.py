"""Operational health endpoint."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiohttp import web


class HealthHandler:
    def __init__(
        self,
        *,
        gateway_snapshot: Callable[[], dict[str, object]],
        database_is_open: Callable[[], bool],
        ai_backend: str,
        novelai_snapshot: Callable[[], dict[str, bool]] | None = None,
        message_storage_snapshot: Callable[[], Awaitable[dict[str, int]]] | None = None,
    ) -> None:
        self._gateway_snapshot = gateway_snapshot
        self._database_is_open = database_is_open
        self._ai_backend = ai_backend
        self._novelai_snapshot = novelai_snapshot
        self._message_storage_snapshot = message_storage_snapshot

    async def serve(self, _request: web.Request) -> web.Response:
        gateway = self._gateway_snapshot()
        novelai = self._novelai_snapshot() if self._novelai_snapshot else {}
        healthy = bool(gateway.get("connected")) and self._database_is_open()
        storage: dict[str, object] = {}
        if self._message_storage_snapshot is not None:
            try:
                storage = await self._message_storage_snapshot()
            except Exception:
                storage = {"available": False}
                healthy = False
        return web.json_response(
            {
                "status": "ok" if healthy else "degraded",
                "service": "AiQQ",
                "qq_gateway": gateway,
                "group_message_database_open": self._database_is_open(),
                "message_storage": storage,
                "ai_backend": self._ai_backend,
                "novelai": {
                    "configured": bool(novelai.get("configured")),
                    "ready": bool(novelai.get("ready")),
                },
                "conversation_context": "group_history_reference",
                "shared_ai_thread": False,
                "private_chat_enabled": False,
            },
            status=200 if healthy else 503,
        )
