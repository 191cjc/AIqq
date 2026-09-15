"""Parsing for explicit group commands supported by the new QQ interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


MENU_COMMAND = "菜单"
GPT_IMAGE_COMMAND = "GPT生图"
NOVELAI_IMAGE_COMMAND = "NovelAI生图"
NOVELAI_PROMPT_COMMAND = "NovelAI提示词"
NOVELAI_PROMPT_EDIT_COMMAND = "修改NovelAI提示词"

CommandName = Literal[
    "menu",
    "gpt_image",
    "novelai_image",
    "novelai_prompt",
    "novelai_prompt_edit",
]


@dataclass(frozen=True)
class ParsedCommand:
    name: CommandName
    argument: str = ""
    token: str = ""


def parse_command(content: str) -> ParsedCommand | None:
    normalized = content.strip()
    for label, name in (
        (NOVELAI_PROMPT_EDIT_COMMAND, "novelai_prompt_edit"),
        (NOVELAI_PROMPT_COMMAND, "novelai_prompt"),
        (NOVELAI_IMAGE_COMMAND, "novelai_image"),
        (GPT_IMAGE_COMMAND, "gpt_image"),
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
    return None


def _command_argument(content: str, label: str) -> str | None:
    for prefix in (label, f"/{label}"):
        if content == prefix:
            return ""
        suffix = content[len(prefix) :] if content.startswith(prefix) else ""
        if suffix and suffix[0].isspace():
            return suffix.strip()
    return None
