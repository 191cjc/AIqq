"""Render and send a completed conversation through QQ group APIs."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from aiqq.logic.models import ConversationResult, ImageAsset
from aiqq.logic.ports import GroupMessageSender
from aiqq.services.images.qq_upload import prepare_qq_image

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
    _public_url: str
    _send_result: Callable[[ConversationResult, str | None], Awaitable[int]]

    async def publish(self, content: str) -> None:
        progress = content.strip() or "任务仍在处理中。"
        await self._replace(f"{DEFERRED_PROGRESS_HEADING}\n\n{progress}", pending=True)

    async def finish(self, result: ConversationResult) -> None:
        full_reply_url = self._public_url
        try:
            await self._replace(_complete_text(result), pending=False)
        except Exception as exc:
            logger.warning(
                "event=deferred_reply_update_failed error_type=%s", type(exc).__name__
            )
            full_reply_url = None
        await self._send_result(result, full_reply_url)

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
        try:
            await self._sender.send_markdown_reply(
                group_id=group_id,
                source_message_id=source_message_id,
                content=mention + DEFERRED_NOTICE_TEXT,
                msg_seq=first_msg_seq,
                keyboard=quick_menu_keyboard(full_reply_url=exported.public_url),
                record_content=DEFERRED_NOTICE_TEXT,
                fallback_member_openid=member_openid,
            )
        except Exception as exc:
            # The final result still has to be delivered if this notice fails.
            logger.warning(
                "event=deferred_reply_notice_failed error_type=%s", type(exc).__name__
            )

        async def send_result(
            result: ConversationResult, full_reply_url: str | None
        ) -> int:
            return await self.send(
                result,
                group_id=group_id,
                source_message_id=source_message_id,
                member_openid=member_openid,
                first_msg_seq=first_msg_seq + 1,
                full_reply_url=full_reply_url,
            )

        return DeferredReply(
            self._reply_store, exported.file_name, exported.public_url, send_result
        )

    async def send(
        self,
        result: ConversationResult,
        *,
        group_id: str,
        source_message_id: str,
        member_openid: str,
        first_msg_seq: int,
        keyboard_factory: Callable[[str | None], Mapping[str, Any]] | None = None,
        full_reply_url: str | None = None,
    ) -> int:
        # Keep completed image bytes in temporary storage even if QQ rejects text.
        prepared_images: list[tuple[str, str, dict[str, Any]] | None] = []
        for image in result.images:
            try:
                prepared_image = await asyncio.to_thread(prepare_qq_image, image)
                exported = await self._media_store.save(prepared_image)
                metadata = {
                    "source": {**_image_metadata(image), "provenance": dict(result.image_provenance)},
                    "delivery": _image_metadata(prepared_image),
                    "compressed": prepared_image.data != image.data,
                }
                prepared_images.append((exported.public_url, prepared_image.mime_type, metadata))
            except Exception as exc:
                logger.warning(
                    "event=qq_image_delivery_failed error_type=%s", type(exc).__name__
                )
                prepared_images.append(None)

        complete_text = _complete_text(result)
        display_text = complete_text
        if len(complete_text) > LONG_REPLY_THRESHOLD:
            display_text = result.summary.strip()
            if full_reply_url is None:
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
            fallback_member_openid=member_openid,
        )
        next_sequence = first_msg_seq + 1

        for prepared in prepared_images:
            delivered = False
            if prepared is not None:
                public_url, mime_type, metadata = prepared
                try:
                    await self._sender.send_image_reply(
                        group_id=group_id,
                        source_message_id=source_message_id,
                        public_url=public_url,
                        mime_type=mime_type,
                        msg_seq=next_sequence,
                        fallback_member_openid=member_openid,
                        image_metadata=metadata,
                    )
                    delivered = True
                except Exception as exc:
                    logger.warning(
                        "event=qq_image_delivery_failed error_type=%s", type(exc).__name__
                    )
            if not delivered:
                await self._sender.send_text_reply(
                    group_id=group_id,
                    source_message_id=source_message_id,
                    content=IMAGE_DELIVERY_FAILED_TEXT,
                    msg_seq=next_sequence,
                    fallback_member_openid=member_openid,
                )
            next_sequence += 1
        return next_sequence


def _image_metadata(image: ImageAsset) -> dict[str, Any]:
    return {
        "width": image.width, "height": image.height,
        "size": len(image.data), "content_type": image.mime_type,
        "sha256": hashlib.sha256(image.data).hexdigest(),
    }


def _complete_text(result: ConversationResult) -> str:
    if not result.sources:
        return result.full_text.strip()
    source_lines = [
        f"- [{source.title}]({source.url})" for source in result.sources
    ]
    return result.full_text.strip() + "\n\n参考来源：\n" + "\n".join(source_lines)
