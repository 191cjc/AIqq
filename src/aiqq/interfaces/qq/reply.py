"""Render and send a completed conversation through QQ group APIs."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from aiqq.logic.models import ConversationResult, ImageAsset
from aiqq.logic.ports import GroupMessageSender

from .ui import quick_menu_keyboard


logger = logging.getLogger(__name__)
LONG_REPLY_THRESHOLD = 50
FULL_REPLY_UNAVAILABLE_TEXT = "完整回答链接暂时不可用。"
IMAGE_DELIVERY_FAILED_TEXT = "图片处理失败，暂时无法发送到 QQ。"
DEFERRED_NOTICE_TEXT = "任务处理时间过长，后续进展请看这里"
DEFERRED_INITIAL_TEXT = "# 任务处理中\n\nCodex 任务仍在继续执行。"
DEFERRED_PROGRESS_HEADING = "# 任务处理中"


class ReplyExport(Protocol):
    file_name: str
    public_url: str


class ReplyStore(Protocol):
    async def save(self, content: str) -> ReplyExport: ...

    async def create_pending(self, content: str) -> ReplyExport: ...

    async def update(
        self, file_name: str, content: str, *, pending: bool
    ) -> bool: ...


class MediaExport(Protocol):
    public_url: str


class MediaStore(Protocol):
    async def save(self, image: ImageAsset) -> MediaExport: ...


@dataclass
class DeferredReply:
    _store: ReplyStore
    _file_name: str

    async def publish(self, content: str) -> None:
        progress = content.strip() or "任务仍在处理中。"
        await self._replace(f"{DEFERRED_PROGRESS_HEADING}\n\n{progress}", pending=True)

    async def finish(self, result: ConversationResult) -> None:
        await self._replace(_complete_text(result), pending=False)

    async def fail(self, content: str) -> None:
        await self._replace(content.strip(), pending=False)

    async def _replace(self, content: str, *, pending: bool) -> None:
        if not await self._store.update(self._file_name, content, pending=pending):
            raise RuntimeError("deferred reply file is no longer available")


class ConversationReplySender:
    def __init__(
        self,
        *,
        sender: GroupMessageSender,
        reply_store: ReplyStore,
        media_store: MediaStore,
    ) -> None:
        self._sender = sender
        self._reply_store = reply_store
        self._media_store = media_store

    async def start_deferred(
        self,
        *,
        group_id: str,
        source_message_id: str,
        member_openid: str,
        first_msg_seq: int,
    ) -> DeferredReply:
        exported = await self._reply_store.create_pending(DEFERRED_INITIAL_TEXT)
        mention = f"<@{member_openid}> " if member_openid else ""
        await self._sender.send_markdown_reply(
            group_id=group_id,
            source_message_id=source_message_id,
            content=mention + DEFERRED_NOTICE_TEXT,
            msg_seq=first_msg_seq,
            keyboard=quick_menu_keyboard(full_reply_url=exported.public_url),
            record_content=DEFERRED_NOTICE_TEXT,
        )
        return DeferredReply(self._reply_store, exported.file_name)

    async def send(
        self,
        result: ConversationResult,
        *,
        group_id: str,
        source_message_id: str,
        member_openid: str,
        first_msg_seq: int,
        keyboard_factory: Callable[[str | None], Mapping[str, Any]] | None = None,
    ) -> int:
        complete_text = _complete_text(result)
        display_text = complete_text
        full_reply_url = None
        if len(complete_text) > LONG_REPLY_THRESHOLD:
            display_text = result.summary.strip()
            try:
                exported = await self._reply_store.save(complete_text)
                full_reply_url = exported.public_url
            except Exception as exc:
                logger.warning(
                    "event=full_reply_store_failed error_type=%s", type(exc).__name__
                )
                display_text += f"\n\n{FULL_REPLY_UNAVAILABLE_TEXT}"

        mention = f"<@{member_openid}> " if member_openid else ""
        keyboard = (
            dict(keyboard_factory(full_reply_url))
            if keyboard_factory is not None
            else quick_menu_keyboard(full_reply_url=full_reply_url)
        )
        await self._sender.send_markdown_reply(
            group_id=group_id,
            source_message_id=source_message_id,
            content=mention + display_text,
            msg_seq=first_msg_seq,
            keyboard=keyboard,
            record_content=complete_text,
        )
        next_sequence = first_msg_seq + 1

        for image in result.images:
            try:
                exported = await self._media_store.save(image)
                await self._sender.send_image_reply(
                    group_id=group_id,
                    source_message_id=source_message_id,
                    public_url=exported.public_url,
                    mime_type=image.mime_type,
                    msg_seq=next_sequence,
                )
            except Exception as exc:
                logger.warning(
                    "event=qq_image_delivery_failed error_type=%s", type(exc).__name__
                )
                await self._sender.send_text_reply(
                    group_id=group_id,
                    source_message_id=source_message_id,
                    content=IMAGE_DELIVERY_FAILED_TEXT,
                    msg_seq=next_sequence,
                )
            next_sequence += 1
        return next_sequence


def _complete_text(result: ConversationResult) -> str:
    if not result.sources:
        return result.full_text.strip()
    source_lines = [
        f"- [{source.title}]({source.url})" for source in result.sources
    ]
    return result.full_text.strip() + "\n\n参考来源：\n" + "\n".join(source_lines)
