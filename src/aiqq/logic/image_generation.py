"""Business workflow for the explicit GPT image command."""

from __future__ import annotations

import logging

from aiqq.exceptions import ImageAuditUnavailable, ImageGenerationUnavailable

from .models import ConversationResult, ImageAction, ImageUsageDecision
from .ports import GeneratedImageService, ImagePromptAuditor, ImageUsageLimiter


logger = logging.getLogger(__name__)
IMAGE_COMMAND_USAGE = "请在 /GPT生图 后写明要生成的画面。"
IMAGE_AUDIT_UNAVAILABLE = "图片审核服务暂时不可用，请稍后再试。"
IMAGE_GENERATION_UNAVAILABLE = "图片生成服务暂时不可用，请稍后再试。"
IMAGE_GENERATION_BUSY = "上一张图片仍在生成，请稍后再试。"
IMAGE_FAILURE_MESSAGES = {
    "authentication": "图片服务暂不可用，需要管理员检查配置。",
    "not_found": "图片服务请求失败，暂时无法生成。",
    "rate_limited": "图片服务请求受限，请稍后再试。",
    "invalid_request": "图片服务未接受本次请求。",
    "timeout": "图片服务响应超时，本次未收到图片。",
    "connection": "暂时无法连接图片服务。",
    "upstream": "图片服务暂时异常，请稍后再试。",
    "invalid_response": "图片服务返回了无法使用的结果。",
}


class GPTImageWorkflow:
    def __init__(
        self,
        *,
        prompt_auditor: ImagePromptAuditor,
        image_service: GeneratedImageService,
        image_usage: ImageUsageLimiter,
    ) -> None:
        self._prompt_auditor = prompt_auditor
        self._image_service = image_service
        self._image_usage = image_usage

    async def run(self, prompt: str, *, conversation_key: str) -> ConversationResult:
        normalized = prompt.strip()
        if not normalized:
            return ConversationResult(
                "rejected", IMAGE_COMMAND_USAGE, IMAGE_COMMAND_USAGE
            )
        try:
            audit = await self._prompt_auditor.audit(normalized)
        except ImageAuditUnavailable:
            return _unavailable(IMAGE_AUDIT_UNAVAILABLE, "image_audit_unavailable")
        if not audit.safe or not audit.effective:
            detail = audit.reason.strip()
            if audit.suggested_prompt.strip():
                detail += f"\n\n可改为：{audit.suggested_prompt.strip()}"
            summary = "图片提示词未通过审核。"
            return ConversationResult("rejected", detail, summary)

        try:
            decision = await self._image_usage.reserve_attempt(conversation_key)
        except Exception as exc:
            logger.warning(
                "event=image_quota_check_failed error_type=%s", type(exc).__name__
            )
            return _unavailable(
                IMAGE_GENERATION_UNAVAILABLE, "image_quota_unavailable"
            )
        if not decision.allowed:
            return _quota_denied(decision)

        try:
            image = await self._image_service.generate(ImageAction("generate", normalized))
            await self._image_usage.record_success(conversation_key)
        except ImageGenerationUnavailable as exc:
            logger.warning(
                "event=gpt_image_generation_failed error_kind=%s attempt_id=%s",
                exc.kind,
                exc.attempt_id or "unknown",
            )
            return image_failure_result(exc)
        except Exception as exc:
            logger.warning(
                "event=gpt_image_generation_failed error_type=%s", type(exc).__name__
            )
            return _unavailable(
                IMAGE_GENERATION_UNAVAILABLE, "image_generation_unavailable"
            )
        finally:
            await self._image_usage.finish_attempt(conversation_key)
        text = "图片已经生成完成。"
        return ConversationResult("ok", text, text, images=(image,))


def image_failure_result(error: ImageGenerationUnavailable) -> ConversationResult:
    """Expose the same fixed, safe image failure in both GPT workflows."""
    text = IMAGE_FAILURE_MESSAGES.get(error.kind)
    if text is None:
        return _unavailable(
            IMAGE_GENERATION_UNAVAILABLE, "image_generation_unavailable"
        )
    return _unavailable(text, f"image_service_{error.kind}")


def _unavailable(text: str, error_code: str) -> ConversationResult:
    return ConversationResult(
        "unavailable", text, text, error_code=error_code
    )


def _quota_denied(decision: ImageUsageDecision) -> ConversationResult:
    if decision.reason == "daily_limit":
        text = f"今天的生图额度已用完（每日 {decision.daily_limit} 张）。"
    else:
        text = IMAGE_GENERATION_BUSY
    return ConversationResult("rejected", text, text)
