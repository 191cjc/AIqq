"""Parsing for explicit group commands supported by the new QQ interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


MENU_COMMAND = "菜单"
GPT_IMAGE_COMMAND = "GPT生图"
GPT_USAGE_COMMAND = "GPT用量"
NOVELAI_IMAGE_COMMAND = "NovelAI生图"
NOVELAI_STYLE_COMMAND = "NovelAI画风"
NOVELAI_PROMPT_COMMAND = "NovelAI提示词"
NOVELAI_QUICK_IMAGE_COMMAND = "NovelAI快速生图"
NOVELAI_PROMPT_EDIT_COMMAND = "修改NovelAI提示词"
NAI_V5_IMAGE_COMMAND = "Nai v5 作图"
NAI_V5_IMAGE_ALIAS = "Nai v5"
NAI_V5_STYLE_COMMAND = "Nai v5 画风"
# QQ clients may insert zero-width characters right after a bot mention.
ZERO_WIDTH_CHARACTERS = "\u200b\u200c\u200d\u2060\ufeff"

CommandName = Literal[
    "menu",
    "gpt_image",
    "gpt_usage",
    "novelai_image",
    "novelai_style",
    "novelai_prompt",
    "novelai_quick_image",
    "novelai_prompt_edit",
    "nai_v5_image",
    "nai_v5_style",
]


@dataclass(frozen=True)
class ParsedCommand:
    name: CommandName
    argument: str = ""
    token: str = ""


def parse_command(content: str) -> ParsedCommand | None:
    normalized = content.strip().lstrip(ZERO_WIDTH_CHARACTERS).strip()
    for label, name in (
        (NOVELAI_PROMPT_EDIT_COMMAND, "novelai_prompt_edit"),
        (NOVELAI_QUICK_IMAGE_COMMAND, "novelai_quick_image"),
        (NOVELAI_PROMPT_COMMAND, "novelai_prompt"),
        (NOVELAI_IMAGE_COMMAND, "novelai_image"),
        (NOVELAI_STYLE_COMMAND, "novelai_style"),
        (NAI_V5_IMAGE_COMMAND, "nai_v5_image"),
        (NAI_V5_STYLE_COMMAND, "nai_v5_style"),
        (GPT_IMAGE_COMMAND, "gpt_image"),
        (GPT_USAGE_COMMAND, "gpt_usage"),
        (MENU_COMMAND, "menu"),
        ("功能菜单", "menu"),
    ):
        argument = _command_argument(normalized, label)
        if argument is None:
            continue
        if name == "novelai_prompt_edit":
            token, separator, request = argument.partition(" ")
            return ParsedCommand(
                name=name,
                token=token,
                argument=request.strip() if separator else "",
            )
        return ParsedCommand(name=name, argument=argument)
    # Match the short drawing alias only after the explicit subcommands.
    # A malformed subcommand must not become an image scene via the alias.
    for label in (NAI_V5_IMAGE_COMMAND, NAI_V5_STYLE_COMMAND):
        if any(_starts_with(normalized, prefix) for prefix in (label, f"/{label}")):
            return None
    argument = _command_argument(normalized, NAI_V5_IMAGE_ALIAS)
    if argument is not None:
        return ParsedCommand(name="nai_v5_image", argument=argument)
    return None


def _command_argument(content: str, label: str) -> str | None:
    for prefix in (label, f"/{label}"):
        if content.casefold() == prefix.casefold():
            return ""
        suffix = content[len(prefix) :] if _starts_with(content, prefix) else ""
        if suffix and suffix[0].isspace():
            return suffix.strip()
    return None


def _starts_with(content: str, prefix: str) -> bool:
    """Match command labels case-insensitively, e.g. ``NovelAi画风``."""
    return content[: len(prefix)].casefold() == prefix.casefold()
