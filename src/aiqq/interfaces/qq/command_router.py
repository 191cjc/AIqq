"""Dispatch explicit QQ commands without mixing them into conversation logic."""

from __future__ import annotations

from typing import Protocol

from aiqq.logic.models import ConversationResult, NovelAIPromptWorkflowResult

from .commands import ParsedCommand
from .handlers import IncomingGroupMessage
from .reply import ConversationReplySender
from .ui import novelai_prompt_options_keyboard


class ImageWorkflow(Protocol):
    async def run(
        self, prompt: str, *, conversation_key: str
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
        novelai_image_workflow: ImageWorkflow,
        novelai_prompt_workflow: PromptWorkflow,
    ) -> None:
        self._reply_sender = reply_sender
        self._gpt_image_workflow = gpt_image_workflow
        self._novelai_image_workflow = novelai_image_workflow
        self._novelai_prompt_workflow = novelai_prompt_workflow

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
            "novelai_prompt",
            "novelai_prompt_edit",
        }:
            return False
        conversation_key = f"group:{message.group_id}:member:{message.member_openid}"
        prompt_result = None
        if command.name == "gpt_image":
            result = await self._gpt_image_workflow.run(
                command.argument, conversation_key=conversation_key
            )
        elif command.name == "novelai_image":
            result = await self._novelai_image_workflow.run(
                command.argument, conversation_key=conversation_key
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
            first_msg_seq=first_msg_seq,
            keyboard_factory=keyboard_factory,
        )
        return True
