"""QQ group send/recall boundary with local ownership enforcement."""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from botpy.errors import ServerError
from botpy.http import Route

from aiqq.logic.models import BotSentGroupMessage
from aiqq.logic.ports import BotMessageRepository


logger = logging.getLogger(__name__)
EXPIRED_REPLY_MESSAGES = frozenset({
    "msgid已经过期,不能回复",  # QQ 40034031
    "回复消息msg_id已过期",  # QQ 40034005
})


class QQMessageSender:
    def __init__(
        self,
        api: Any,
        repository: BotMessageRepository,
        *,
        bot_username: str,
        recall_window_seconds: int = 120,
    ) -> None:
        self._api = api
        self._repository = repository
        self._bot_username = bot_username
        self._recall_window = timedelta(seconds=recall_window_seconds)

    def set_bot_username(self, username: str) -> None:
        if username.strip():
            self._bot_username = username.strip()

    async def send_proactive_text(
        self,
        *,
        group_id: str,
        content: str,
        origin: str = "console",
    ) -> BotSentGroupMessage:
        return await self._send_and_record(
            group_id=group_id,
            source_message_id="",
            msg_seq=None,
            message_type=0,
            visible_content=content,
            record_content=content,
            request={"content": content},
            progress=False,
            origin=origin,
        )

    async def send_text_reply(
        self,
        *,
        group_id: str,
        source_message_id: str,
        content: str,
        msg_seq: int,
        progress: bool = False,
        origin: str = "conversation",
        fallback_member_openid: str = "",
    ) -> BotSentGroupMessage:
        return await self._send_and_record(
            group_id=group_id,
            source_message_id=source_message_id,
            msg_seq=msg_seq,
            message_type=0,
            visible_content=content,
            record_content=content,
            request={"content": content},
            progress=progress,
            origin=origin,
            fallback_member_openid=fallback_member_openid,
        )

    async def send_markdown_reply(
        self,
        *,
        group_id: str,
        source_message_id: str,
        content: str,
        msg_seq: int,
        keyboard: dict[str, Any] | None = None,
        record_content: str | None = None,
        origin: str = "conversation",
        fallback_member_openid: str = "",
    ) -> BotSentGroupMessage:
        request: dict[str, Any] = {"markdown": {"content": content}}
        if keyboard is not None:
            request["keyboard"] = keyboard
        return await self._send_and_record(
            group_id=group_id,
            source_message_id=source_message_id,
            msg_seq=msg_seq,
            message_type=2,
            visible_content=content,
            record_content=record_content or content,
            request=request,
            progress=False,
            origin=origin,
            fallback_member_openid=fallback_member_openid,
        )

    async def send_image_reply(
        self,
        *,
        group_id: str,
        source_message_id: str,
        public_url: str,
        mime_type: str,
        msg_seq: int,
        origin: str = "conversation",
        fallback_member_openid: str = "",
        image_metadata: Mapping[str, Any] | None = None,
    ) -> BotSentGroupMessage:
        media = await self._deliver(
            self._api.post_group_file,
            operation="upload_image", group_id=group_id,
            source_message_id=source_message_id,
            parameters={
                "group_openid": group_id, "file_type": 1,
                "url": public_url, "srv_send_msg": False,
            },
            metadata=dict(image_metadata or {}),
        )
        file_info = _field(media, "file_info")
        if not isinstance(file_info, str) or not file_info:
            raise RuntimeError("QQ media upload response omitted file_info")
        attachment = {
            "content_type": mime_type,
            "filename": public_url.rsplit("/", 1)[-1],
            "url": public_url,
        }
        if image_metadata:
            attachment.update(dict(image_metadata.get("delivery", {})))
            attachment["image_metadata"] = dict(image_metadata)
        return await self._send_and_record(
            group_id=group_id,
            source_message_id=source_message_id,
            msg_seq=msg_seq,
            message_type=7,
            visible_content="[图片]",
            record_content="[图片]",
            request={"media": {"file_info": file_info}},
            progress=False,
            origin=origin,
            fallback_member_openid=fallback_member_openid,
            attachments=(attachment,),
        )

    async def _send_and_record(
        self,
        *,
        group_id: str,
        source_message_id: str,
        msg_seq: int | None,
        message_type: int,
        visible_content: str,
        record_content: str,
        request: dict[str, Any],
        progress: bool,
        origin: str,
        attachments: tuple[dict[str, Any], ...] = (),
        fallback_member_openid: str = "",
    ) -> BotSentGroupMessage:
        send_request = {
            "group_openid": group_id,
            "msg_type": message_type,
            **request,
        }
        if source_message_id:
            if msg_seq is None:
                raise ValueError("reply messages require msg_seq")
            send_request.update(msg_id=source_message_id, msg_seq=msg_seq)
        delivery_mode = "reply" if source_message_id else "proactive"
        fallback_reason = None
        try:
            response = await self._deliver(
                self._api.post_group_message, operation="send_message",
                group_id=group_id, source_message_id=source_message_id,
                parameters=send_request,
                metadata={"origin": origin, "attachments": list(attachments)},
            )
            message_id = _require_message_id(response)
        except ServerError as exc:
            # botpy discards QQ's numeric code, keeping only the error message.
            # Retry only known definite rejections, never an ambiguous send outcome.
            if (
                not source_message_id
                or not fallback_member_openid
                or progress
                or str(exc) not in EXPIRED_REPLY_MESSAGES
            ):
                raise
            request = dict(request)
            if message_type == 2:
                request["markdown"] = {
                    **request["markdown"],
                    "content": _mention_member(
                        request["markdown"]["content"], fallback_member_openid
                    ),
                }
                visible_content = request["markdown"]["content"]
            elif message_type == 0:
                request["content"] = _mention_member(
                    request.get("content", ""), fallback_member_openid
                )
                visible_content = _mention_member(
                    visible_content, fallback_member_openid
                )
            # Explicit None also overrides botpy's default msg_seq=1 on the wire.
            send_request.update(request, msg_id=None, msg_seq=None)
            logger.info(
                "event=qq_expired_reply_fallback status=attempt message_type=%s",
                message_type,
            )
            try:
                response = await self._deliver(
                    self._api.post_group_message, operation="send_message",
                    group_id=group_id, source_message_id=source_message_id,
                    parameters=send_request,
                    metadata={"origin": origin, "fallback_reason": "expired_msg_id", "attachments": list(attachments)},
                )
                message_id = _require_message_id(response)
            except Exception as fallback_exc:
                logger.warning(
                    "event=qq_expired_reply_fallback status=failed message_type=%s error_type=%s",
                    message_type,
                    type(fallback_exc).__name__,
                )
                raise
            delivery_mode = "proactive"
            fallback_reason = "expired_msg_id"
            msg_seq = None
            logger.info(
                "event=qq_expired_reply_fallback status=success message_type=%s",
                message_type,
            )
        sent_at = _parse_timestamp(_field(response, "timestamp"))
        payload = {
            "id": message_id,
            "group_openid": group_id,
            "message_type": message_type,
            "timestamp": sent_at.isoformat(timespec="seconds"),
            "source_message_id": source_message_id,
            "content": visible_content,
            "author": {"bot": True, "username": self._bot_username},
            "origin": origin,
            "delivery_mode": delivery_mode,
            **request,
        }
        if fallback_reason is not None:
            payload["fallback_reason"] = fallback_reason
        if msg_seq is not None:
            payload["msg_seq"] = msg_seq
        if attachments:
            payload["attachments"] = list(attachments)
        try:
            recorded = await self._repository.add_bot_message(
                message_id=message_id,
                group_openid=group_id,
                username=self._bot_username,
                content=record_content,
                message_type=message_type,
                sent_at=sent_at.isoformat(timespec="seconds"),
                payload=payload,
                source_message_id=source_message_id,
                progress=progress,
            )
        except Exception as exc:
            recorded = False
            logger.warning(
                "event=bot_message_record_failed error_type=%s", type(exc).__name__
            )
        return BotSentGroupMessage(group_id, message_id, sent_at, recorded)

    async def _deliver(
        self, call: Callable[..., Awaitable[Any]], *, operation: str,
        group_id: str, source_message_id: str,
        parameters: dict[str, Any], metadata: dict[str, Any],
    ) -> Any:
        """Keep actual attempts separately from confirmed message records."""
        attempt_id = secrets.token_hex(12)
        stored_parameters = {
            "attempt_id": attempt_id, "request": dict(parameters), "metadata": metadata,
        }
        context = dict(
            group_id=group_id, source_message_id=source_message_id,
            operation=operation, parameters=stored_parameters,
        )
        await self._record_attempt(**context, status="started")
        try:
            response = await call(**parameters)
            if operation == "send_message":
                _require_message_id(response)
            elif operation == "upload_image" and not _field(response, "file_info"):
                raise RuntimeError("QQ media upload response omitted file_info")
        except asyncio.CancelledError:
            await self._record_attempt(**context, status="cancelled", error_type="CancelledError")
            raise
        except Exception as exc:
            await self._record_attempt(**context, status="failed", error_type=type(exc).__name__)
            raise
        await self._record_attempt(
            **context, status="succeeded", result=_business_response(response),
            message_id=_field(response, "id") or "",
        )
        return response

    async def _record_attempt(self, **fields: Any) -> None:
        try:
            await self._repository.record_delivery_attempt(**fields)
        except Exception as exc:
            logger.warning("event=delivery_attempt_record_failed error_type=%s", type(exc).__name__)

    async def recall_from_group(self, target: BotSentGroupMessage) -> bool:
        if not target.recorded:
            return False
        now = datetime.now(timezone.utc)
        sent_at = target.sent_at
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
        if now - sent_at.astimezone(timezone.utc) >= self._recall_window:
            return False
        try:
            owned = await self._repository.is_owned_bot_message(
                group_id=target.group_id, message_id=target.message_id
            )
        except Exception as exc:
            logger.warning(
                "event=bot_message_ownership_check_failed error_type=%s",
                type(exc).__name__,
            )
            return False
        if not owned:
            return False
        route = Route(
            "DELETE",
            "/v2/groups/{group_openid}/messages/{message_id}",
            group_openid=target.group_id,
            message_id=target.message_id,
        )
        try:
            await self._api._http.request(route)
            recorded = await self._repository.mark_recalled(
                group_id=target.group_id,
                message_id=target.message_id,
                recalled_at=now.isoformat(timespec="milliseconds"),
            )
        except Exception as exc:
            logger.warning(
                "event=bot_message_recall_failed error_type=%s", type(exc).__name__
            )
            return False
        return recorded


def _mention_member(content: str, member_openid: str) -> str:
    mention = f"<@{member_openid}>"
    if content.startswith(mention):
        return content
    return f"{mention} {content}".rstrip()


def _business_response(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list, str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {"unavailable_response_type": type(value).__name__}


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _require_message_id(response: Any) -> str:
    message_id = _field(response, "id")
    if not isinstance(message_id, str) or not message_id:
        raise RuntimeError("QQ send response omitted the message ID")
    return message_id


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)
