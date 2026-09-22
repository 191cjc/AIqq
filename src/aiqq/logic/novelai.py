"""NovelAI prompt preparation and image-generation workflows."""

from __future__ import annotations

import logging

from aiqq.exceptions import (
    ImageAuditUnavailable,
    ImageGenerationUnavailable,
    NovelAIPromptInvalidOutput,
    NovelAIPromptUnavailable,
    NovelAITagSearchUnavailable,
)

from .image_generation import _quota_denied, notify_image_start
from .models import (
    ConversationResult,
    ImagePromptAuditResult,
    NovelAIPromptOptions,
    NovelAIPromptWorkflowResult,
)
from .ports import (
    ImagePromptAuditor,
    ImageStartCallback,
    ImageUsageLimiter,
    NovelAIImageGenerator,
    NovelAIPromptGenerator,
    NovelAIPromptSessionRepository,
    NovelAIStyleStore,
)


logger = logging.getLogger(__name__)
NOVELAI_IMAGE_USAGE = "请在 /NovelAI生图 后填写英文提示词。"
NOVELAI_PROMPT_USAGE = "请在 /NovelAI提示词 后写明想要的画面。"
NOVELAI_QUICK_IMAGE_USAGE = "请在 /NovelAI快速生图 后写明想要的画面。"
NOVELAI_PROMPT_EDIT_USAGE = "请通过提示词方案中的修改按钮填写修改要求。"
NOVELAI_AUDIT_UNAVAILABLE = "图片审核服务暂时不可用，请稍后再试。"
NOVELAI_GENERATION_UNAVAILABLE = "NovelAI 图片生成暂时不可用，请稍后再试。"
NOVELAI_GENERATION_ERRORS = {
    "not_configured": "NovelAI 生图服务未配置，请联系管理员。",
    "not_ready": "NovelAI 生图服务连接未就绪，请稍后再试。",
}
NOVELAI_PROMPT_UNAVAILABLE = "NovelAI 提示词服务暂时不可用，请稍后再试。"
NOVELAI_PROMPT_INVALID_OUTPUT = {
    "planning": "NovelAI 标签查询词生成格式有误，请重试或调整描述。",
    "authoring": "NovelAI 提示词生成格式有误，请重试或调整描述。",
}
NOVELAI_TAG_SEARCH_UNAVAILABLE = "Danbooru 标签查询失败，请稍后重试。"
NOVELAI_SESSION_EXPIRED = "这次提示词修改已过期，请重新生成提示词方案。"
NOVELAI_STYLE_LOAD_UNAVAILABLE = "NovelAI 画风读取失败，本次未开始生图，请稍后重试。"


class NovelAIImageWorkflow:
    def __init__(
        self,
        *,
        prompt_auditor: ImagePromptAuditor,
        image_service: NovelAIImageGenerator,
        image_usage: ImageUsageLimiter,
        styles: NovelAIStyleStore,
    ) -> None:
        self._prompt_auditor = prompt_auditor
        self._image_service = image_service
        self._image_usage = image_usage
        self._styles = styles

    async def run(
        self, prompt: str, *, conversation_key: str, member_openid: str,
        on_image_start: ImageStartCallback | None = None,
    ) -> ConversationResult:
        normalized = prompt.strip()
        if not normalized:
            return ConversationResult(
                "rejected", NOVELAI_IMAGE_USAGE, NOVELAI_IMAGE_USAGE
            )
        try:
            style = await self._styles.load(member_openid)
        except Exception as exc:
            logger.warning(
                "event=novelai_style_load_failed error_type=%s", type(exc).__name__
            )
            return _unavailable(
                NOVELAI_STYLE_LOAD_UNAVAILABLE, "novelai_style_unavailable"
            )
        if style:
            normalized = f"{style}, {normalized}"
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
            await notify_image_start(on_image_start)
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
        authored = await self.author(description)
        if isinstance(authored, ConversationResult):
            return _prompt_result(authored)
        try:
            session = await self._sessions.create(conversation_key, authored)
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
        return _successful_prompt_result(authored, session)

    async def author(
        self, description: str
    ) -> NovelAIPromptOptions | ConversationResult:
        normalized = description.strip()
        if not normalized:
            return ConversationResult(
                "rejected", NOVELAI_PROMPT_USAGE, NOVELAI_PROMPT_USAGE
            )
        try:
            audit = await self._prompt_auditor.audit(normalized)
        except ImageAuditUnavailable:
            return _unavailable(
                NOVELAI_AUDIT_UNAVAILABLE, "image_audit_unavailable"
            )
        if not audit.safe or not audit.effective:
            return _audit_rejection(audit)
        try:
            return await self._prompt_agent.generate(normalized)
        except NovelAITagSearchUnavailable:
            return _unavailable(
                NOVELAI_TAG_SEARCH_UNAVAILABLE, "novelai_tag_search_unavailable"
            )
        except NovelAIPromptInvalidOutput as exc:
            return _unavailable(
                NOVELAI_PROMPT_INVALID_OUTPUT[exc.stage],
                "novelai_prompt_invalid_output",
            )
        except NovelAIPromptUnavailable:
            return _unavailable(
                NOVELAI_PROMPT_UNAVAILABLE, "novelai_prompt_unavailable"
            )
        except Exception as exc:
            logger.warning(
                "event=novelai_prompt_session_create_failed error_type=%s",
                type(exc).__name__,
            )
            return _unavailable(
                NOVELAI_PROMPT_UNAVAILABLE, "novelai_prompt_session_unavailable"
            )

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
        except NovelAITagSearchUnavailable:
            return _prompt_result(
                _unavailable(
                    NOVELAI_TAG_SEARCH_UNAVAILABLE, "novelai_tag_search_unavailable"
                )
            )
        except NovelAIPromptInvalidOutput as exc:
            return _prompt_result(
                _unavailable(
                    NOVELAI_PROMPT_INVALID_OUTPUT[exc.stage], "novelai_prompt_invalid_output"
                )
            )
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


class NovelAIQuickImageWorkflow:
    def __init__(
        self,
        *,
        prompt_workflow: NovelAIPromptWorkflow,
        image_workflow: NovelAIImageWorkflow,
    ) -> None:
        self._prompt_workflow = prompt_workflow
        self._image_workflow = image_workflow

    async def run(
        self,
        description: str,
        *,
        conversation_key: str,
        member_openid: str,
        on_image_start: ImageStartCallback | None = None,
    ) -> ConversationResult:
        normalized = description.strip()
        if not normalized:
            return ConversationResult(
                "rejected", NOVELAI_QUICK_IMAGE_USAGE, NOVELAI_QUICK_IMAGE_USAGE
            )
        authored = await self._prompt_workflow.author(normalized)
        if isinstance(authored, ConversationResult):
            return authored
        return await self._image_workflow.run(
            authored.prompts[0],
            conversation_key=conversation_key,
            member_openid=member_openid,
            on_image_start=on_image_start,
        )


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
