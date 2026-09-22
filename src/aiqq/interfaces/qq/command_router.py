"""Dispatch explicit QQ commands without mixing them into conversation logic."""

from __future__ import annotations

from typing import Protocol

from aiqq.logic.models import ConversationResult, NovelAIPromptWorkflowResult
from aiqq.logic.ports import ImageStartCallback

from .commands import ParsedCommand
from .handlers import IncomingGroupMessage
from .reply import ConversationReplySender
from .ui import novelai_prompt_options_keyboard


class ImageWorkflow(Protocol):
    async def run(
        self, prompt: str, *, conversation_key: str,
        on_image_start: ImageStartCallback | None = None,
    ) -> ConversationResult: ...


class NovelAIImageWorkflow(Protocol):
    async def run(
        self, prompt: str, *, conversation_key: str, member_openid: str,
        on_image_start: ImageStartCallback | None = None,
    ) -> ConversationResult: ...


class NovelAIQuickImageWorkflow(Protocol):
    async def run(
        self, description: str, *, conversation_key: str, member_openid: str,
        on_image_start: ImageStartCallback | None = None,
    ) -> ConversationResult: ...


class StyleWorkflow(Protocol):
    async def run(
        self, prompt: str, *, member_openid: str
    ) -> ConversationResult: ...


class PromptWorkflow(Protocol):
    async def create(
        self, description: str, *, conversation_key: str
    ) -> NovelAIPromptWorkflowResult: ...

    async def revise(
        self, token: str, request: str, *, conversation_key: str
    ) -> NovelAIPromptWorkflowResult: ...


class ExplicitCommandRouter:
    def __init__(
        self,
        *,
        reply_sender: ConversationReplySender,
        gpt_image_workflow: ImageWorkflow,
        novelai_image_workflow: NovelAIImageWorkflow,
        novelai_quick_image_workflow: NovelAIQuickImageWorkflow,
        novelai_prompt_workflow: PromptWorkflow,
        novelai_style_workflow: StyleWorkflow,
    ) -> None:
        self._reply_sender = reply_sender
        self._gpt_image_workflow = gpt_image_workflow
        self._novelai_image_workflow = novelai_image_workflow
        self._novelai_quick_image_workflow = novelai_quick_image_workflow
        self._novelai_prompt_workflow = novelai_prompt_workflow
        self._novelai_style_workflow = novelai_style_workflow

    async def handle(
        self,
        command: ParsedCommand,
        message: IncomingGroupMessage,
        *,
        first_msg_seq: int,
    ) -> bool:
        if command.name not in {
            "gpt_image",
            "novelai_image",
            "novelai_quick_image",
            "novelai_style",
            "novelai_prompt",
            "novelai_prompt_edit",
        }:
            return False
        conversation_key = f"group:{message.group_id}:member:{message.member_openid}"
        next_msg_seq = first_msg_seq
        image_started = False

        async def on_image_start() -> None:
            nonlocal next_msg_seq, image_started
            if image_started:
                return
            image_started = True
            sequence = next_msg_seq
            next_msg_seq += 1
            await self._reply_sender.send_image_start(
                group_id=message.group_id,
                source_message_id=message.message_id,
                member_openid=message.member_openid,
                msg_seq=sequence,
            )

        prompt_result = None
        if command.name == "gpt_image":
            result = await self._gpt_image_workflow.run(
                command.argument, conversation_key=conversation_key,
                on_image_start=on_image_start,
            )
        elif command.name == "novelai_image":
            result = await self._novelai_image_workflow.run(
                command.argument,
                conversation_key=conversation_key,
                member_openid=message.member_openid,
                on_image_start=on_image_start,
            )
        elif command.name == "novelai_quick_image":
            result = await self._novelai_quick_image_workflow.run(
                command.argument,
                conversation_key=conversation_key,
                member_openid=message.member_openid,
                on_image_start=on_image_start,
            )
        elif command.name == "novelai_style":
            result = await self._novelai_style_workflow.run(
                command.argument, member_openid=message.member_openid
            )
        elif command.name == "novelai_prompt":
            prompt_result = await self._novelai_prompt_workflow.create(
                command.argument, conversation_key=conversation_key
            )
            result = prompt_result.result
        else:
            prompt_result = await self._novelai_prompt_workflow.revise(
                command.token,
                command.argument,
                conversation_key=conversation_key,
            )
            result = prompt_result.result

        keyboard_factory = None
        if prompt_result is not None and prompt_result.session is not None:
            session = prompt_result.session
            keyboard_factory = lambda full_url: novelai_prompt_options_keyboard(
                session.options.prompts,
                session.token,
                full_reply_url=full_url,
            )
        await self._reply_sender.send(
            result,
            group_id=message.group_id,
            source_message_id=message.message_id,
            member_openid=message.member_openid,
            first_msg_seq=next_msg_seq,
            keyboard_factory=keyboard_factory,
        )
        return True
