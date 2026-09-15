"""QQ group send/recall boundary with local ownership enforcement."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from botpy.http import Route

from aiqq.logic.models import BotSentGroupMessage
from aiqq.logic.ports import BotMessageRepository


logger = logging.getLogger(__name__)


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
    ) -> BotSentGroupMessage:
        media = await self._api.post_group_file(
            group_openid=group_id,
            file_type=1,
            url=public_url,
            srv_send_msg=False,
        )
        file_info = _field(media, "file_info")
        if not isinstance(file_info, str) or not file_info:
            raise RuntimeError("QQ media upload response omitted file_info")
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
            attachments=(
                {
                    "content_type": mime_type,
                    "filename": public_url.rsplit("/", 1)[-1],
                    "url": public_url,
                },
            ),
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
        response = await self._api.post_group_message(**send_request)
        message_id = _field(response, "id")
        if not isinstance(message_id, str) or not message_id:
            raise RuntimeError("QQ send response omitted the message ID")
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
            **request,
        }
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


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)
