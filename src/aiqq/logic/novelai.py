"""NovelAI prompt preparation and image-generation workflows."""

from __future__ import annotations

import logging

from aiqq.exceptions import (
    ImageAuditUnavailable,
    ImageGenerationUnavailable,
    NovelAIPromptUnavailable,
)

from .image_generation import _quota_denied
from .models import (
    ConversationResult,
    ImagePromptAuditResult,
    NovelAIPromptOptions,
    NovelAIPromptWorkflowResult,
)
from .ports import (
    ImagePromptAuditor,
    ImageUsageLimiter,
    NovelAIImageGenerator,
    NovelAIPromptGenerator,
    NovelAIPromptSessionRepository,
)


logger = logging.getLogger(__name__)
NOVELAI_IMAGE_USAGE = "请在 /NovelAI生图 后填写英文提示词。"
NOVELAI_PROMPT_USAGE = "请在 /NovelAI提示词 后写明想要的画面。"
NOVELAI_PROMPT_EDIT_USAGE = "请通过提示词方案中的修改按钮填写修改要求。"
NOVELAI_AUDIT_UNAVAILABLE = "图片审核服务暂时不可用，请稍后再试。"
NOVELAI_GENERATION_UNAVAILABLE = "NovelAI 图片生成暂时不可用，请稍后再试。"
NOVELAI_GENERATION_ERRORS = {
    "not_configured": "NovelAI 生图服务未配置，请联系管理员。",
    "not_ready": "NovelAI 生图服务连接未就绪，请稍后再试。",
}
NOVELAI_PROMPT_UNAVAILABLE = "NovelAI 提示词服务暂时不可用，请稍后再试。"
NOVELAI_SESSION_EXPIRED = "这次提示词修改已过期，请重新生成提示词方案。"


class NovelAIImageWorkflow:
    def __init__(
        self,
        *,
        prompt_auditor: ImagePromptAuditor,
        image_service: NovelAIImageGenerator,
        image_usage: ImageUsageLimiter,
    ) -> None:
        self._prompt_auditor = prompt_auditor
        self._image_service = image_service
        self._image_usage = image_usage

    async def run(self, prompt: str, *, conversation_key: str) -> ConversationResult:
        normalized = prompt.strip()
        if not normalized:
            return ConversationResult(
                "rejected", NOVELAI_IMAGE_USAGE, NOVELAI_IMAGE_USAGE
            )
        try:
            audit = await self._prompt_auditor.audit(
                normalized, require_english=True
            )
        except ImageAuditUnavailable:
            return _unavailable(NOVELAI_AUDIT_UNAVAILABLE, "image_audit_unavailable")
        if not normalized.isascii() or not audit.safe or not audit.effective:
            return _audit_rejection(audit, require_english=not normalized.isascii())

        try:
            decision = await self._image_usage.reserve_attempt(conversation_key)
        except Exception as exc:
            logger.warning(
                "event=image_quota_check_failed provider=novelai error_type=%s",
                type(exc).__name__,
            )
            return _unavailable(
                NOVELAI_GENERATION_UNAVAILABLE, "image_quota_unavailable"
            )
        if not decision.allowed:
            return _quota_denied(decision)

        try:
            image = await self._image_service.generate(
                normalized, orientation=audit.orientation
            )
            await self._image_usage.record_success(conversation_key)
        except ImageGenerationUnavailable as exc:
            kind = exc.kind if exc.kind in NOVELAI_GENERATION_ERRORS else "unknown"
            logger.warning("event=novelai_generation_failed error_kind=%s", kind)
            return _unavailable(
                NOVELAI_GENERATION_ERRORS.get(kind, NOVELAI_GENERATION_UNAVAILABLE),
                f"novelai_service_{kind}"
                if kind != "unknown" else "novelai_generation_unavailable",
            )
        except Exception as exc:
            logger.warning(
                "event=novelai_generation_failed error_type=%s", type(exc).__name__
            )
            return _unavailable(
                NOVELAI_GENERATION_UNAVAILABLE, "novelai_generation_unavailable"
            )
        finally:
            await self._image_usage.finish_attempt(conversation_key)
        text = "NovelAI 图片已经生成完成。"
        return ConversationResult(
            "ok", text, text, images=(image,),
            image_provenance={"provider": "novelai", "operation": "generate"},
        )


class NovelAIPromptWorkflow:
    def __init__(
        self,
        *,
        prompt_auditor: ImagePromptAuditor,
        prompt_agent: NovelAIPromptGenerator,
        sessions: NovelAIPromptSessionRepository,
    ) -> None:
        self._prompt_auditor = prompt_auditor
        self._prompt_agent = prompt_agent
        self._sessions = sessions

    async def create(
        self, description: str, *, conversation_key: str
    ) -> NovelAIPromptWorkflowResult:
        normalized = description.strip()
        if not normalized:
            return _prompt_result(
                ConversationResult(
                    "rejected", NOVELAI_PROMPT_USAGE, NOVELAI_PROMPT_USAGE
                )
            )
        try:
            audit = await self._prompt_auditor.audit(normalized)
        except ImageAuditUnavailable:
            return _prompt_result(
                _unavailable(NOVELAI_AUDIT_UNAVAILABLE, "image_audit_unavailable")
            )
        if not audit.safe or not audit.effective:
            return _prompt_result(_audit_rejection(audit))
        try:
            options = await self._prompt_agent.generate(normalized)
            session = await self._sessions.create(conversation_key, options)
        except NovelAIPromptUnavailable:
            return _prompt_result(
                _unavailable(
                    NOVELAI_PROMPT_UNAVAILABLE, "novelai_prompt_unavailable"
                )
            )
        except Exception as exc:
            logger.warning(
                "event=novelai_prompt_session_create_failed error_type=%s",
                type(exc).__name__,
            )
            return _prompt_result(
                _unavailable(
                    NOVELAI_PROMPT_UNAVAILABLE, "novelai_prompt_session_unavailable"
                )
            )
        return _successful_prompt_result(options, session)

    async def revise(
        self,
        token: str,
        request: str,
        *,
        conversation_key: str,
    ) -> NovelAIPromptWorkflowResult:
        normalized_request = request.strip()
        if not token or not normalized_request:
            return _prompt_result(
                ConversationResult(
                    "rejected",
                    NOVELAI_PROMPT_EDIT_USAGE,
                    NOVELAI_PROMPT_EDIT_USAGE,
                )
            )
        try:
            existing = await self._sessions.load(conversation_key, token)
        except Exception as exc:
            logger.warning(
                "event=novelai_prompt_session_load_failed error_type=%s",
                type(exc).__name__,
            )
            return _prompt_result(
                _unavailable(
                    NOVELAI_PROMPT_UNAVAILABLE, "novelai_prompt_session_unavailable"
                )
            )
        if existing is None:
            return _prompt_result(
                ConversationResult(
                    "rejected", NOVELAI_SESSION_EXPIRED, NOVELAI_SESSION_EXPIRED
                )
            )
        try:
            options = await self._prompt_agent.revise(
                existing.options, normalized_request
            )
            session = await self._sessions.create(conversation_key, options)
        except NovelAIPromptUnavailable:
            return _prompt_result(
                _unavailable(
                    NOVELAI_PROMPT_UNAVAILABLE, "novelai_prompt_unavailable"
                )
            )
        except Exception as exc:
            logger.warning(
                "event=novelai_prompt_revision_store_failed error_type=%s",
                type(exc).__name__,
            )
            return _prompt_result(
                _unavailable(
                    NOVELAI_PROMPT_UNAVAILABLE, "novelai_prompt_session_unavailable"
                )
            )
        return _successful_prompt_result(options, session)


def _audit_rejection(
    audit: ImagePromptAuditResult, *, require_english: bool = False
) -> ConversationResult:
    if require_english:
        detail = "NovelAI 生图提示词必须使用英文。"
    else:
        detail = audit.reason.strip()
    if audit.suggested_prompt.strip():
        detail += f"\n\n可改为：{audit.suggested_prompt.strip()}"
    return ConversationResult("rejected", detail, "图片提示词未通过审核。")


def _successful_prompt_result(options, session) -> NovelAIPromptWorkflowResult:
    lines = [
        "已生成三套 NovelAI 提示词方案：",
        *(f"\n方案{index}\n{prompt}" for index, prompt in enumerate(options.prompts, 1)),
    ]
    result = ConversationResult(
        "ok",
        "\n".join(lines),
        "已生成三套 NovelAI 提示词方案。",
    )
    return NovelAIPromptWorkflowResult(result, session)


def _prompt_result(result: ConversationResult) -> NovelAIPromptWorkflowResult:
    return NovelAIPromptWorkflowResult(result)


def _unavailable(text: str, error_code: str) -> ConversationResult:
    return ConversationResult(
        "unavailable", text, text, error_code=error_code
    )
