"""Application workflow for one explicitly mentioned group conversation."""

from __future__ import annotations

import logging
import secrets

from aiqq.exceptions import (
    ConversationUnavailable,
    ImageGenerationUnavailable,
    PromptAuditUnavailable,
)

from .group_context import prepare_group_context
from .image_generation import _quota_denied, image_failure_result
from .models import ConversationRequest, ConversationResult, ImageAction, ImageAsset
from .ports import (
    ConversationAgent,
    GeneratedImageService,
    GroupHistoryRepository,
    ImagePromptAuditor,
    ImageUsageLimiter,
    ProgressCallback,
    PromptAuditor,
    ReferenceImageLoader,
    WebImageService,
)


logger = logging.getLogger(__name__)
PROMPT_AUDIT_UNAVAILABLE_TEXT = "审核服务暂时不可用，请稍后再试。"
CONVERSATION_UNAVAILABLE_TEXT = "对话服务暂时不可用，请稍后再试。"
IMAGE_UNAVAILABLE_TEXT = "图片处理暂时不可用，请稍后再试。"
OPTIONAL_IMAGE_FAILED_TEXT = "图片处理失败，本次只返回文字内容。"
REJECTION_TEXT = {
    "adult_content": "这个请求不适合在公开群聊中处理，请换一个话题。",
    "persona_override": "固定身份和表达方式不能修改，请直接告诉我需要解决的问题。",
    "complex_research": "这次需要分别检索的目标太多，请缩小范围后再试。",
    "too_many_questions": "一次请只问一个独立问题，我会逐个帮你处理。",
    "too_complex": "这个请求范围太大，请先拆成一个可以单次完成的任务。",
}


class ConversationWorkflow:
    def __init__(
        self,
        *,
        prompt_auditor: PromptAuditor,
        history_repository: GroupHistoryRepository,
        conversation_agent: ConversationAgent,
        image_usage: ImageUsageLimiter,
        image_service: GeneratedImageService | None = None,
        image_prompt_auditor: ImagePromptAuditor | None = None,
        reference_image_loader: ReferenceImageLoader | None = None,
        web_image_service: WebImageService | None = None,
        history_message_limit: int = 50,
        history_char_limit: int = 1000,
    ) -> None:
        self._prompt_auditor = prompt_auditor
        self._history_repository = history_repository
        self._conversation_agent = conversation_agent
        self._image_usage = image_usage
        self._image_service = image_service
        self._image_prompt_auditor = image_prompt_auditor
        self._reference_image_loader = reference_image_loader
        self._web_image_service = web_image_service
        self._history_message_limit = history_message_limit
        self._history_char_limit = history_char_limit

    async def run(
        self,
        *,
        group_id: str,
        before_message_id: str,
        conversation_key: str,
        user_input: str,
        on_progress: ProgressCallback | None = None,
        request_id: str | None = None,
    ) -> ConversationResult:
        current_request_id = request_id or secrets.token_hex(12)
        try:
            audit = await self._prompt_auditor.audit(user_input)
        except PromptAuditUnavailable:
            logger.warning(
                "event=prompt_audit_unavailable request_id=%s", current_request_id
            )
            return _unavailable(
                PROMPT_AUDIT_UNAVAILABLE_TEXT, "prompt_audit_unavailable"
            )
        if not audit.allowed:
            text = REJECTION_TEXT.get(audit.category, "这个请求目前无法使用。")
            return ConversationResult(
                status="rejected",
                full_text=text,
                summary=text,
            )

        reference_available = True
        try:
            raw_history = await self._history_repository.list_for_reference(
                group_id,
                before_message_id=before_message_id,
                limit=self._history_message_limit,
            )
        except Exception as exc:
            reference_available = False
            raw_history = ()
            logger.warning(
                "event=group_history_context_unavailable request_id=%s error_type=%s",
                current_request_id,
                type(exc).__name__,
            )

        if reference_available:
            history = prepare_group_context(
                raw_history,
                request_id=current_request_id,
                message_limit=self._history_message_limit,
                char_limit=self._history_char_limit,
            ).messages
        else:
            history = ()

        request = ConversationRequest(
            user_input=user_input,
            group_history=history,
            reference_material_available=reference_available,
            conversation_key=conversation_key,
        )
        try:
            output = await self._conversation_agent.run(
                request, on_progress=on_progress
            )
        except ConversationUnavailable:
            logger.warning(
                "event=conversation_agent_unavailable request_id=%s",
                current_request_id,
            )
            return _unavailable(CONVERSATION_UNAVAILABLE_TEXT, "conversation_unavailable")

        images = output.generated_images[:1]
        if images:
            quota_result = await self._record_embedded_image(
                conversation_key, current_request_id
            )
            if quota_result is not None:
                return quota_result
        if not images and output.image_action is not None:
            try:
                image = await self._execute_image_action(
                    group_id, conversation_key, output.image_action
                )
            except _ImageQuotaDenied as exc:
                return _quota_denied(exc.decision)
            except ImageGenerationUnavailable as exc:
                logger.warning(
                    "event=conversation_image_action_failed request_id=%s "
                    "error_kind=%s attempt_id=%s",
                    current_request_id,
                    exc.kind,
                    exc.attempt_id or "unknown",
                )
                return image_failure_result(exc)
            except Exception as exc:
                logger.warning(
                    "event=conversation_image_action_failed request_id=%s error_type=%s",
                    current_request_id,
                    type(exc).__name__,
                )
                return _unavailable(IMAGE_UNAVAILABLE_TEXT, "image_processing_unavailable")
            images = (image,)

        full_text = output.full_text
        if (
            not images
            and output.selected_web_image_url is not None
            and self._web_image_service is not None
        ):
            try:
                images = (
                    await self._web_image_service.download(
                        output.selected_web_image_url
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "event=conversation_web_image_failed request_id=%s error_type=%s",
                    current_request_id,
                    type(exc).__name__,
                )
                full_text = full_text.rstrip() + "\n\n" + OPTIONAL_IMAGE_FAILED_TEXT

        return ConversationResult(
            status="ok",
            full_text=full_text,
            summary=output.summary,
            images=images[:1],
            sources=output.sources,
        )

    async def _execute_image_action(
        self, group_id: str, conversation_key: str, action: ImageAction
    ) -> ImageAsset:
        if self._image_service is None:
            raise RuntimeError("generated image service is not configured")
        if self._image_prompt_auditor is None:
            raise RuntimeError("image prompt auditor is not configured")
        audit = await self._image_prompt_auditor.audit(action.prompt)
        if not audit.safe or not audit.effective:
            raise RuntimeError("generated image prompt was rejected")
        reference = None
        if action.mode == "edit":
            if self._reference_image_loader is None or action.source_record_id is None:
                raise RuntimeError("reference image loader is not configured")
            reference = await self._reference_image_loader.load(
                group_id, action.source_record_id
            )
            if reference is None:
                raise RuntimeError("referenced image is no longer available")
        decision = await self._image_usage.reserve_attempt(conversation_key)
        if not decision.allowed:
            raise _ImageQuotaDenied(decision)
        try:
            image = await self._image_service.generate(
                action, reference_image=reference
            )
            await self._image_usage.record_success(conversation_key)
            return image
        finally:
            await self._image_usage.finish_attempt(conversation_key)

    async def _record_embedded_image(
        self, conversation_key: str, request_id: str
    ) -> ConversationResult | None:
        try:
            decision = await self._image_usage.reserve_attempt(conversation_key)
            if not decision.allowed:
                return _quota_denied(decision)
            try:
                await self._image_usage.record_success(conversation_key)
            finally:
                await self._image_usage.finish_attempt(conversation_key)
        except Exception as exc:
            logger.warning(
                "event=conversation_image_accounting_failed request_id=%s error_type=%s",
                request_id,
                type(exc).__name__,
            )
            return _unavailable(IMAGE_UNAVAILABLE_TEXT, "image_quota_unavailable")
        return None


class _ImageQuotaDenied(RuntimeError):
    def __init__(self, decision) -> None:
        super().__init__(decision.reason or "image quota denied")
        self.decision = decision


def _unavailable(text: str, error_code: str) -> ConversationResult:
    return ConversationResult(
        status="unavailable",
        full_text=text,
        summary=text,
        error_code=error_code,
    )
