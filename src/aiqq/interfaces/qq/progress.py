"""Per-conversation replacement lifecycle for temporary QQ progress messages."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from aiqq.logic.models import BotSentGroupMessage
from aiqq.logic.ports import GroupMessageSender


logger = logging.getLogger(__name__)


class QQProgressReporter:
    def __init__(
        self,
        sender: GroupMessageSender,
        *,
        group_id: str,
        source_message_id: str,
        first_msg_seq: int = 1,
        max_updates: int = 2,
        cleanup_seconds: int = 90,
    ) -> None:
        self._sender = sender
        self._group_id = group_id
        self._source_message_id = source_message_id
        self._next_msg_seq = first_msg_seq
        self._max_updates = max_updates
        self._cleanup_seconds = cleanup_seconds
        self._published_count = 0
        self._current: BotSentGroupMessage | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._disabled = False

    @property
    def next_msg_seq(self) -> int:
        return self._next_msg_seq

    async def publish(self, content: str) -> None:
        async with self._lock:
            if (
                self._closed
                or self._disabled
                or self._published_count >= self._max_updates
            ):
                return
            self._cancel_cleanup()
            if self._current is not None:
                if not await self._sender.recall_from_group(self._current):
                    self._disabled = True
                    logger.warning("event=progress_replacement_disabled")
                    return
                self._current = None
            sent = await self._sender.send_text_reply(
                group_id=self._group_id,
                source_message_id=self._source_message_id,
                content=content,
                msg_seq=self._next_msg_seq,
                progress=True,
            )
            self._next_msg_seq += 1
            self._published_count += 1
            self._current = sent
            if not sent.recorded:
                self._disabled = True
                return
            self._cleanup_task = asyncio.create_task(self._expire(sent))

    async def finish(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cancel_cleanup()
            current = self._current
            self._current = None
            if current is not None:
                await self._sender.recall_from_group(current)

    async def _expire(self, target: BotSentGroupMessage) -> None:
        try:
            await asyncio.sleep(self._cleanup_seconds)
            async with self._lock:
                if self._current != target:
                    return
                if await self._sender.recall_from_group(target):
                    self._current = None
        except asyncio.CancelledError:
            raise

    def _cancel_cleanup(self) -> None:
        task = self._cleanup_task
        self._cleanup_task = None
        if task is not None and not task.done():
            task.cancel()

    async def __aenter__(self) -> "QQProgressReporter":
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.finish()

    async def wait_for_cleanup(self) -> None:
        task = self._cleanup_task
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task
