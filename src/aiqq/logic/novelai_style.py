"""Save or clear the current sender's persistent NovelAI style."""

from __future__ import annotations

import logging

from .models import ConversationResult
from .ports import NovelAIStyleStore


logger = logging.getLogger(__name__)
NOVELAI_STYLE_ENGLISH_REQUIRED = "NovelAI 画风必须使用英文提示词，原有画风未更改。"
NOVELAI_STYLE_SAVE_UNAVAILABLE = "NovelAI 画风保存失败，请稍后重新发送 /NovelAI画风 指令。"
NOVELAI_STYLE_CLEAR_UNAVAILABLE = "NovelAI 画风清除失败，请稍后重新发送空的 /NovelAI画风 指令。"
NOVELAI_STYLE_SAVED = (
    "已保存并启用你的 NovelAI 画风，后续生图会自动添加。"
    "再次输入会覆盖；发送空的 /NovelAI画风 可清除。"
)
NOVELAI_STYLE_CLEARED = "已清除你的 NovelAI 画风，后续生图将仅使用本次提示词。"


class NovelAIStyleWorkflow:
    def __init__(self, *, styles: NovelAIStyleStore) -> None:
        self._styles = styles

    async def run(self, prompt: str, *, member_openid: str) -> ConversationResult:
        normalized = prompt.strip()
        if normalized and not normalized.isascii():
            return ConversationResult(
                "rejected",
                NOVELAI_STYLE_ENGLISH_REQUIRED,
                NOVELAI_STYLE_ENGLISH_REQUIRED,
            )
        try:
            await self._styles.save(member_openid, normalized)
        except Exception as exc:
            logger.warning(
                "event=novelai_style_save_failed error_type=%s", type(exc).__name__
            )
            text = (
                NOVELAI_STYLE_SAVE_UNAVAILABLE
                if normalized else NOVELAI_STYLE_CLEAR_UNAVAILABLE
            )
            return ConversationResult(
                "unavailable", text, text, error_code="novelai_style_unavailable"
            )
        text = NOVELAI_STYLE_SAVED if normalized else NOVELAI_STYLE_CLEARED
        summary = "已保存并启用你的 NovelAI 画风。" if normalized else NOVELAI_STYLE_CLEARED
        return ConversationResult("ok", text, summary)
