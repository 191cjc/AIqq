"""Thin qq-botpy client wired to the new application workflows."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import botpy
from aiohttp import web
from botpy.message import GroupMessage

from aiqq.config import QQConfig, WebConfig
from aiqq.services.qq.panel import PanelService
from aiqq.services.qq.sender import QQMessageSender

from .gateway import GatewayStatus, MonitoredBotWebSocket
from .handlers import (
    GroupMessageHandler,
    IncomingGroupMessage,
    normalized_group_message_data,
    raw_event_mentions_bot,
)


logger = logging.getLogger(__name__)
AsyncCallback = Callable[[], Awaitable[None]]


class GatewayMessageRepository(Protocol):
    @property
    def is_open(self) -> bool: ...

    async def add_gateway_event(
        self, event_type: str, gateway_payload: dict[str, Any]
    ) -> bool: ...


@dataclass(frozen=True)
class ClientComponents:
    group_messages: GatewayMessageRepository
    message_handler: GroupMessageHandler
    sender: QQMessageSender
    panel: PanelService
    web_application: web.Application
    initializers: tuple[AsyncCallback, ...]
    closers: tuple[AsyncCallback, ...]


class AiQQClient(botpy.Client):
    def __init__(
        self,
        *,
        qq_config: QQConfig,
        web_config: WebConfig,
        component_factory: Callable[["AiQQClient"], ClientComponents],
    ) -> None:
        super().__init__(
            intents=botpy.Intents(public_messages=True),
            timeout=qq_config.http_timeout_seconds,
            ext_handlers=False,
        )
        self._qq_config = qq_config
        self._web_config = web_config
        self.gateway_status = GatewayStatus()
        self._components = component_factory(self)
        self._ready_lock = asyncio.Lock()
        self._initialized = False
        self._web_runner: web.AppRunner | None = None
        self._gateway_watchdog: asyncio.Task[None] | None = None

    async def bot_connect(self, session: Any) -> None:
        socket = MonitoredBotWebSocket(
            session,
            self._connection,
            on_connected=self._mark_gateway_connected,
            on_disconnected=self._mark_gateway_disconnected,
            on_group_message=self._handle_raw_group_message,
        )
        try:
            await socket.ws_connect()
        except (Exception, KeyboardInterrupt, SystemExit) as exc:
            await socket.on_error(exc)

    async def on_ready(self) -> None:
        async with self._ready_lock:
            if self._initialized:
                return
            for initialize in self._components.initializers:
                await initialize()
            bot_name = getattr(self.robot, "name", "") or "AiQQ"
            self._components.sender.set_bot_username(bot_name)
            try:
                panel_result = await self._components.panel.sync()
                logger.info(
                    "event=qq_panel_synchronized action=%s panel_id=%s",
                    panel_result.action,
                    panel_result.panel_id or "unknown",
                )
            except Exception as exc:
                logger.warning(
                    "event=qq_panel_sync_failed error_type=%s", type(exc).__name__
                )
            await self._start_web_server()
            self._initialized = True
            logger.info("event=aiqq_ready bot_name=%s", bot_name)

    async def on_group_at_message_create(self, message: GroupMessage) -> None:
        await self._components.message_handler.handle(_incoming(message))

    async def on_group_msg_receive(self, _event: object) -> None:
        logger.info("event=qq_full_group_messages_enabled")

    async def on_group_msg_reject(self, _event: object) -> None:
        logger.info("event=qq_full_group_messages_disabled")

    async def _handle_raw_group_message(
        self, event_type: str, payload: dict[str, Any]
    ) -> None:
        try:
            saved = await self._components.group_messages.add_gateway_event(
                event_type, payload
            )
        except Exception as exc:
            logger.warning(
                "event=group_message_record_failed event_type=%s error_type=%s",
                event_type,
                type(exc).__name__,
            )
            saved = False
        if not saved:
            return
        if event_type != "GROUP_MESSAGE_CREATE" or not raw_event_mentions_bot(payload):
            return
        data = normalized_group_message_data(payload)
        if data is None:
            return
        message = GroupMessage(self.api, payload.get("id"), data)
        await self._components.message_handler.handle(_incoming(message))

    def _mark_gateway_connected(self, resumed: bool) -> None:
        self.gateway_status.mark_connected(resumed=resumed)
        task = self._gateway_watchdog
        if task is not None and not task.done():
            task.cancel()
        self._gateway_watchdog = None

    def _mark_gateway_disconnected(self, code: int | None, message: str) -> None:
        self.gateway_status.mark_disconnected(code, message)
        task = self._gateway_watchdog
        if task is None or task.done():
            self._gateway_watchdog = self.loop.create_task(
                self._restart_if_gateway_stuck(), name="aiqq-gateway-watchdog"
            )

    async def _restart_if_gateway_stuck(self) -> None:
        try:
            await asyncio.sleep(self._qq_config.gateway_restart_timeout_seconds)
        except asyncio.CancelledError:
            return
        if not self.gateway_status.connected:
            logger.error("event=qq_gateway_restart_timeout")
            os.kill(os.getpid(), signal.SIGTERM)

    async def _start_web_server(self) -> None:
        if self._web_runner is not None:
            return
        runner = web.AppRunner(self._components.web_application)
        await runner.setup()
        try:
            await web.TCPSite(
                runner, host=self._web_config.host, port=self._web_config.port
            ).start()
        except Exception:
            await runner.cleanup()
            raise
        self._web_runner = runner
        logger.info(
            "event=http_server_started host=%s port=%s",
            self._web_config.host,
            self._web_config.port,
        )

    async def close(self) -> None:
        if self.is_closed():
            return
        watchdog = self._gateway_watchdog
        self._gateway_watchdog = None
        if watchdog is not None and not watchdog.done():
            watchdog.cancel()
        if self._web_runner is not None:
            await self._web_runner.cleanup()
            self._web_runner = None
        results = await asyncio.gather(
            *(close() for close in self._components.closers),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                logger.warning(
                    "event=application_resource_close_failed error_type=%s",
                    type(result).__name__,
                )
        await super().close()


def _incoming(message: GroupMessage) -> IncomingGroupMessage:
    author = getattr(message, "author", None)
    return IncomingGroupMessage(
        group_id=str(getattr(message, "group_openid", "") or ""),
        message_id=str(getattr(message, "id", "") or ""),
        member_openid=str(getattr(author, "member_openid", "") or ""),
        content=str(getattr(message, "content", "") or ""),
    )
