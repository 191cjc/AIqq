"""QQ group-message entry point for commands and ordinary conversations."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from aiqq.logic.conversation import CONVERSATION_UNAVAILABLE_TEXT
from aiqq.logic.models import ConversationResult
from aiqq.logic.ports import GroupMessageSender

from .commands import ParsedCommand, parse_command
from .progress import QQProgressReporter
from .reply import ConversationReplySender
from .ui import feature_menu_keyboard


logger = logging.getLogger(__name__)
BOT_MENTION_RE = re.compile(r"^\s*<@!?[^>]+>\s*")
MAX_TRACKED_MESSAGE_IDS = 2048
DEFERRED_LINK_UNAVAILABLE_TEXT = "任务处理时间过长，但进展页面暂时不可用。"


@dataclass(frozen=True)
class IncomingGroupMessage:
    group_id: str
    message_id: str
    member_openid: str
    content: str
    has_attachments: bool = False


class ConversationRunner(Protocol):
    async def run(self, **kwargs: object) -> ConversationResult: ...


class ExplicitCommandHandler(Protocol):
    async def handle(
        self,
        command: ParsedCommand,
        message: IncomingGroupMessage,
        *,
        first_msg_seq: int,
    ) -> bool: ...


class DeferredReplyHandle(Protocol):
    async def publish(self, content: str) -> None: ...

    async def finish(self, result: ConversationResult) -> None: ...

    async def fail(self, content: str) -> None: ...


class _ProgressRouter:
    def __init__(self, reporter: QQProgressReporter) -> None:
        self._target: Callable[[str], Awaitable[None]] = reporter.publish
        self._lock = asyncio.Lock()

    async def publish(self, content: str) -> None:
        async with self._lock:
            target = self._target
        await target(content)

    async def defer_to(self, reply: DeferredReplyHandle) -> None:
        async with self._lock:
            self._target = reply.publish


class GroupMessageHandler:
    def __init__(
        self,
        *,
        workflow: ConversationRunner,
        sender: GroupMessageSender,
        reply_sender: ConversationReplySender,
        defer_after_seconds: float,
        command_handler: ExplicitCommandHandler | None = None,
    ) -> None:
        if defer_after_seconds <= 0:
            raise ValueError("defer_after_seconds must be positive")
        self._workflow = workflow
        self._sender = sender
        self._reply_sender = reply_sender
        self._defer_after_seconds = defer_after_seconds
        self._command_handler = command_handler
        self._seen_message_ids: dict[tuple[str, str], None] = {}
        self._dedup_lock = asyncio.Lock()
        self._deferred_tasks: set[asyncio.Task[None]] = set()

    async def handle(self, message: IncomingGroupMessage) -> bool:
        if not await self._claim(message.group_id, message.message_id):
            return False
        prompt = clean_prompt(message.content)
        if not prompt and message.has_attachments:
            prompt = "请查看我这条消息中的图片或表情，并描述内容。"
        if not prompt:
            await self._sender.send_text_reply(
                group_id=message.group_id,
                source_message_id=message.message_id,
                content="请告诉我需要处理的问题。",
                msg_seq=1,
            )
            return True

        command = parse_command(prompt)
        if command is not None:
            if command.name == "menu":
                await self._sender.send_markdown_reply(
                    group_id=message.group_id,
                    source_message_id=message.message_id,
                    content="请选择要使用的功能。",
                    msg_seq=1,
                    keyboard=feature_menu_keyboard(),
                )
                return True
            if self._command_handler is not None and await self._command_handler.handle(
                command, message, first_msg_seq=1
            ):
                return True
            await self._sender.send_text_reply(
                group_id=message.group_id,
                source_message_id=message.message_id,
                content="该功能暂时不可用。",
                msg_seq=1,
            )
            return True

        reporter = QQProgressReporter(
            self._sender,
            group_id=message.group_id,
            source_message_id=message.message_id,
        )
        progress = _ProgressRouter(reporter)
        workflow_task = asyncio.create_task(
            self._workflow.run(
                group_id=message.group_id,
                before_message_id=message.message_id,
                conversation_key=(
                    f"group:{message.group_id}:member:{message.member_openid}"
                ),
                user_input=prompt,
                on_progress=progress.publish,
            ),
            name=f"aiqq-conversation-{message.message_id}",
        )
        try:
            done, _pending = await asyncio.wait(
                {workflow_task}, timeout=self._defer_after_seconds
            )
        except asyncio.CancelledError:
            workflow_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await workflow_task
            raise

        if not done:
            await reporter.finish()
            try:
                deferred = await self._reply_sender.start_deferred(
                    group_id=message.group_id,
                    source_message_id=message.message_id,
                    member_openid=message.member_openid,
                    first_msg_seq=reporter.next_msg_seq,
                )
                await progress.defer_to(deferred)
            except Exception as exc:
                logger.warning(
                    "event=deferred_reply_start_failed error_type=%s",
                    type(exc).__name__,
                )
                self._track_deferred(
                    self._complete_without_deferred(
                        workflow_task,
                        message,
                        first_msg_seq=reporter.next_msg_seq + 1,
                    )
                )
                try:
                    await self._sender.send_text_reply(
                        group_id=message.group_id,
                        source_message_id=message.message_id,
                        content=DEFERRED_LINK_UNAVAILABLE_TEXT,
                        msg_seq=reporter.next_msg_seq,
                        fallback_member_openid=message.member_openid,
                    )
                except Exception as notice_exc:
                    logger.warning(
                        "event=deferred_reply_notice_failed error_type=%s",
                        type(notice_exc).__name__,
                    )
                return True
            self._track_deferred(
                self._complete_deferred(workflow_task, deferred)
            )
            logger.info(
                "event=group_conversation_deferred message_id=%s", message.message_id
            )
            return True

        try:
            result = workflow_task.result()
        except Exception as exc:
            logger.warning(
                "event=group_conversation_failed error_type=%s", type(exc).__name__
            )
            result = ConversationResult(
                "unavailable",
                CONVERSATION_UNAVAILABLE_TEXT,
                CONVERSATION_UNAVAILABLE_TEXT,
                error_code="conversation_unavailable",
            )
        finally:
            await reporter.finish()

        try:
            await self._reply_sender.send(
                result,
                group_id=message.group_id,
                source_message_id=message.message_id,
                member_openid=message.member_openid,
                first_msg_seq=reporter.next_msg_seq,
            )
        except Exception as exc:
            logger.warning(
                "event=group_reply_delivery_failed error_type=%s", type(exc).__name__
            )
        return True

    def _track_deferred(self, coroutine: Awaitable[None]) -> None:
        task = asyncio.create_task(coroutine, name="aiqq-deferred-conversation")
        self._deferred_tasks.add(task)
        task.add_done_callback(self._deferred_tasks.discard)

    @staticmethod
    async def _complete_deferred(
        workflow_task: asyncio.Task[ConversationResult],
        deferred: DeferredReplyHandle,
    ) -> None:
        try:
            result = await workflow_task
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await deferred.fail(CONVERSATION_UNAVAILABLE_TEXT)
            raise
        except Exception as exc:
            logger.warning(
                "event=deferred_conversation_failed error_type=%s", type(exc).__name__
            )
            result = ConversationResult(
                "unavailable",
                CONVERSATION_UNAVAILABLE_TEXT,
                CONVERSATION_UNAVAILABLE_TEXT,
                error_code="conversation_unavailable",
            )
        try:
            await deferred.finish(result)
        except Exception as exc:
            logger.warning(
                "event=deferred_reply_delivery_failed error_type=%s", type(exc).__name__
            )

    async def _complete_without_deferred(
        self,
        workflow_task: asyncio.Task[ConversationResult],
        message: IncomingGroupMessage,
        *,
        first_msg_seq: int,
    ) -> None:
        try:
            result = await workflow_task
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "event=deferred_conversation_unreported error_type=%s", type(exc).__name__
            )
            result = ConversationResult(
                "unavailable",
                CONVERSATION_UNAVAILABLE_TEXT,
                CONVERSATION_UNAVAILABLE_TEXT,
                error_code="conversation_unavailable",
            )
        try:
            await self._reply_sender.send(
                result,
                group_id=message.group_id,
                source_message_id=message.message_id,
                member_openid=message.member_openid,
                first_msg_seq=first_msg_seq,
            )
        except Exception as exc:
            logger.warning(
                "event=deferred_reply_delivery_failed error_type=%s", type(exc).__name__
            )

    async def _claim(self, group_id: str, message_id: str) -> bool:
        if not message_id:
            return False
        async with self._dedup_lock:
            key = (group_id, message_id)
            if key in self._seen_message_ids:
                return False
            self._seen_message_ids[key] = None
            if len(self._seen_message_ids) > MAX_TRACKED_MESSAGE_IDS:
                self._seen_message_ids.pop(next(iter(self._seen_message_ids)))
            return True


def clean_prompt(content: str | None) -> str:
    return BOT_MENTION_RE.sub("", content or "").strip()


def raw_event_mentions_bot(payload: dict[str, object]) -> bool:
    data = payload.get("d")
    if not isinstance(data, dict):
        return False
    mentions = data.get("mentions")
    return isinstance(mentions, list) and any(
        isinstance(mention, dict) and mention.get("is_you") is True
        for mention in mentions
    )


def normalized_group_message_data(payload: dict[str, object]) -> dict | None:
    data = payload.get("d")
    if not isinstance(data, dict):
        return None
    normalized = dict(data)
    content = clean_prompt(str(normalized.get("content") or ""))
    mentions = normalized.get("mentions")
    if isinstance(mentions, list):
        for mention in mentions:
            if not isinstance(mention, dict) or mention.get("is_you") is not True:
                continue
            username = mention.get("username")
            if isinstance(username, str) and content.startswith(f"@{username}"):
                content = content[len(username) + 1 :].lstrip()
                break
    normalized["content"] = content
    return normalized
