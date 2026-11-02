"""Dispatch explicit QQ commands without mixing them into conversation logic."""

from __future__ import annotations

import logging
from typing import Protocol

from aiqq.logic.models import ConversationResult, NovelAIPromptWorkflowResult
from aiqq.logic.ports import ImageStartCallback

from .commands import ParsedCommand
from .handlers import IncomingGroupMessage
from .reply import ConversationReplySender
from .ui import novelai_prompt_options_keyboard


logger = logging.getLogger(__name__)


class ImageWorkflow(Protocol):
    async def run(
        self, prompt: str, *, conversation_key: str,
        member_openid: str = "",
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


class UsageWorkflow(Protocol):
    async def run(self, *, member_openid: str) -> ConversationResult: ...


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
        nai_v5_image_workflow: NovelAIImageWorkflow | None = None,
        nai_v5_quick_image_workflow: NovelAIQuickImageWorkflow | None = None,
        nai_v5_style_workflow: StyleWorkflow | None = None,
        gpt_usage_workflow: UsageWorkflow | None = None,
    ) -> None:
        self._reply_sender = reply_sender
        self._gpt_image_workflow = gpt_image_workflow
        self._novelai_image_workflow = novelai_image_workflow
        self._novelai_quick_image_workflow = novelai_quick_image_workflow
        self._novelai_prompt_workflow = novelai_prompt_workflow
        self._novelai_style_workflow = novelai_style_workflow
        self._nai_v5_image_workflow = nai_v5_image_workflow
        self._nai_v5_quick_image_workflow = nai_v5_quick_image_workflow
        self._nai_v5_style_workflow = nai_v5_style_workflow
        self._gpt_usage_workflow = gpt_usage_workflow

    async def handle(
        self,
        command: ParsedCommand,
        message: IncomingGroupMessage,
        *,
        first_msg_seq: int,
    ) -> bool:
        if command.name not in {
            "gpt_image",
            "gpt_usage",
            "novelai_image",
            "nai_v5_image",
            "novelai_quick_image",
            "novelai_style",
            "nai_v5_style",
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
        show_generation_prompt = command.name in {
            "novelai_image", "novelai_quick_image", "nai_v5_image",
        }
        has_chinese = command.name == "novelai_image" and any(
            "\u3400" <= character <= "\u9fff" for character in command.argument
        )
        if show_generation_prompt and command.argument.strip():
            sequence = next_msg_seq
            next_msg_seq += 1
            # Receipt happens before auditing/translation; do not send another
            # "received" notice from the later provider-start callback.
            image_started = True
            try:
                await self._reply_sender.acknowledge_image_request(
                    group_id=message.group_id,
                    source_message_id=message.message_id,
                    member_openid=message.member_openid,
                    msg_seq=sequence,
                    translate_prompt=has_chinese,
                )
            except Exception as exc:
                logger.warning(
                    "event=image_request_acknowledgement_failed error_type=%s",
                    type(exc).__name__,
                )
        if command.name == "gpt_usage":
            if self._gpt_usage_workflow is None:
                text = "GPT 用量查询暂不可用，请联系管理员。"
                result = ConversationResult(
                    "unavailable", text, text, error_code="gpt_usage_unavailable"
                )
            else:
                result = await self._gpt_usage_workflow.run(
                    member_openid=message.member_openid
                )
        elif command.name == "gpt_image":
            result = await self._gpt_image_workflow.run(
                command.argument, conversation_key=conversation_key,
                member_openid=message.member_openid,
                on_image_start=on_image_start,
            )
        elif command.name == "nai_v5_image":
            workflow = self._nai_v5_image_workflow
            if workflow is None:
                result = ConversationResult(
                    "unavailable", "Nai v5 生图服务未配置，请联系管理员。",
                    "Nai v5 生图服务未配置，请联系管理员。",
                    error_code="nai_v5_service_not_configured",
                )
            else:
                result = await workflow.run(
                    command.argument,
                    conversation_key=conversation_key,
                    member_openid=message.member_openid,
                    on_image_start=on_image_start,
                )
        elif command.name == "novelai_image":
            workflow = (
                self._novelai_quick_image_workflow
                if has_chinese else self._novelai_image_workflow
            )
            result = await workflow.run(
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
        elif command.name == "nai_v5_style":
            if self._nai_v5_style_workflow is None:
                text = "Nai v5 画风功能暂不可用，请联系管理员。"
                result = ConversationResult(
                    "unavailable", text, text, error_code="nai_v5_style_unavailable"
                )
            else:
                result = await self._nai_v5_style_workflow.run(
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
            show_generation_prompt=show_generation_prompt,
        )
        return True
