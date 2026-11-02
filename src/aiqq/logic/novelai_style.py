"""Save or clear the current sender's persistent style for one provider."""

from __future__ import annotations

import logging
import re

from .models import ConversationResult
from .ports import NovelAIStyleStore


logger = logging.getLogger(__name__)

# Artifacts left by copying prompts out of chat/Markdown renderers.
_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")
_MARKDOWN_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!~|>])")


class NovelAIStyleWorkflow:
    def __init__(
        self, *, styles: NovelAIStyleStore,
        provider_name: str = "NovelAI",
        command_name: str = "NovelAI画风",
        provider_id: str = "novelai",
    ) -> None:
        self._styles = styles
        self._provider_name = provider_name
        self._command_name = command_name
        self._provider_id = provider_id

    async def run(self, prompt: str, *, member_openid: str) -> ConversationResult:
        normalized = normalize_style_prompt(prompt)
        if normalized and not normalized.isascii():
            text = f"{self._provider_name} 画风必须使用英文提示词，原有画风未更改。"
            return ConversationResult("rejected", text, text)
        try:
            await self._styles.save(member_openid, normalized)
        except Exception as exc:
            logger.warning(
                "event=%s_style_save_failed error_type=%s",
                self._provider_id, type(exc).__name__,
            )
            text = (
                f"{self._provider_name} 画风保存失败，请稍后重新发送 /{self._command_name} 指令。"
                if normalized else
                f"{self._provider_name} 画风清除失败，请稍后重新发送空的 /{self._command_name} 指令。"
            )
            return ConversationResult(
                "unavailable", text, text,
                error_code=f"{self._provider_id}_style_unavailable",
            )
        if not normalized:
            text = f"已清除你的 {self._provider_name} 画风，后续生图将仅使用本次提示词。"
            return ConversationResult("ok", text, text)
        summary = f"已保存并启用你的 {self._provider_name} 画风。"
        text = (
            f"已保存并启用你的 {self._provider_name} 画风，后续生图会自动添加。"
            f"再次输入会覆盖；发送空的 /{self._command_name} 可清除。"
        )
        return ConversationResult("ok", text, summary)


def normalize_style_prompt(prompt: str) -> str:
    """Undo copy artifacts: ``↵`` line markers, zero-width chars, Markdown escapes."""
    text = prompt.replace("\u21b5", "\n")
    text = _ZERO_WIDTH_RE.sub("", text)
    text = _MARKDOWN_ESCAPE_RE.sub(r"\1", text)
    return text.strip()
