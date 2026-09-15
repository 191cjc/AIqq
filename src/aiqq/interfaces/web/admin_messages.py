"""Loopback-only endpoint used by the console group-message script."""

from __future__ import annotations

import asyncio
import hmac
import re

from aiohttp import web

from aiqq.logic.ports import BotMessageRepository, GroupMessageSender


GROUP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
MAX_CONSOLE_MESSAGE_CHARS = 2000


class AdminMessageHandler:
    def __init__(
        self,
        repository: BotMessageRepository,
        sender: GroupMessageSender,
        *,
        token: str,
    ) -> None:
        self._repository = repository
        self._sender = sender
        self._token = token
        self._send_lock = asyncio.Lock()

    @property
    def is_configured(self) -> bool:
        return bool(self._token)

    async def send_group_message(self, request: web.Request) -> web.Response:
        if request.remote not in {"127.0.0.1", "::1"}:
            return _error("loopback_only", "该接口只允许本机访问。", 403)
        if not self.is_configured:
            return _error("not_configured", "管理消息接口尚未配置。", 503)
        if not self._authorized(request.headers.get("Authorization", "")):
            return _error("unauthorized", "管理 Token 无效。", 401)
        try:
            payload = await request.json()
        except (ValueError, TypeError):
            return _error("invalid_json", "请求体必须是 JSON 对象。", 400)
        if not isinstance(payload, dict):
            return _error("invalid_json", "请求体必须是 JSON 对象。", 400)
        allowed_fields = {"group_openid", "content", "reply_to_message_id"}
        if set(payload) - allowed_fields:
            return _error("invalid_request", "请求包含未知字段。", 400)
        group_id = payload.get("group_openid")
        content = payload.get("content")
        reply_to = payload.get("reply_to_message_id")
        if not isinstance(group_id, str) or not GROUP_ID_RE.fullmatch(group_id):
            return _error("invalid_group", "群 OpenID 格式无效。", 400)
        if (
            not isinstance(content, str)
            or not content.strip()
            or len(content) > MAX_CONSOLE_MESSAGE_CHARS
        ):
            return _error(
                "invalid_content",
                f"正文必须包含 1 至 {MAX_CONSOLE_MESSAGE_CHARS} 个字符。",
                400,
            )
        if reply_to is not None and (
            not isinstance(reply_to, str) or not MESSAGE_ID_RE.fullmatch(reply_to)
        ):
            return _error("invalid_reply_context", "回复消息 ID 格式无效。", 400)

        async with self._send_lock:
            try:
                if reply_to is None:
                    sent = await self._sender.send_proactive_text(
                        group_id=group_id,
                        content=content.strip(),
                        origin="console",
                    )
                else:
                    context = await self._repository.find_console_reply_context(
                        group_id, message_id=reply_to
                    )
                    if context is None:
                        return _error(
                            "no_reply_context",
                            "指定消息不是 5 分钟内可用的 @ 消息上下文。",
                            409,
                        )
                    sent = await self._sender.send_text_reply(
                        group_id=group_id,
                        source_message_id=context.message_id,
                        content=content.strip(),
                        msg_seq=context.next_sequence,
                        origin="console",
                    )
            except Exception as exc:
                return _error(
                    "qq_send_failed",
                    f"QQ 拒绝或未完成发送（{type(exc).__name__}）。",
                    502,
                )
        return web.json_response(
            {
                "ok": True,
                "message_id": sent.message_id,
                "sent_at": sent.sent_at.isoformat(),
                "recorded": sent.recorded,
            }
        )

    def _authorized(self, authorization: str) -> bool:
        scheme, separator, supplied = authorization.partition(" ")
        return (
            separator == " "
            and scheme.lower() == "bearer"
            and bool(supplied)
            and hmac.compare_digest(supplied, self._token)
        )


def register_admin_message_routes(
    application: web.Application, handler: AdminMessageHandler
) -> None:
    application.router.add_post(
        "/internal/group-messages", handler.send_group_message
    )


def _error(code: str, message: str, status: int) -> web.Response:
    return web.json_response(
        {"ok": False, "error": {"code": code, "message": message}},
        status=status,
    )
