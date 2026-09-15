"""QQ keyboard payloads; these contain presentation only."""

from __future__ import annotations

from typing import Any

from .commands import (
    MENU_COMMAND,
    NOVELAI_IMAGE_COMMAND,
    NOVELAI_PROMPT_COMMAND,
    NOVELAI_PROMPT_EDIT_COMMAND,
)


def quick_menu_keyboard(*, full_reply_url: str | None = None) -> dict[str, Any]:
    rows = [
        _command_row(
            button_id="open_feature_menu",
            label="功能菜单",
            command=f"/{MENU_COMMAND}",
            enter=True,
        )
    ]
    if full_reply_url:
        rows.append(_link_row(full_reply_url))
    return {"content": {"rows": rows}}


def feature_menu_keyboard() -> dict[str, Any]:
    return {
        "content": {
            "rows": [
                _command_row(
                    button_id="novelai_prompt",
                    label=NOVELAI_PROMPT_COMMAND,
                    command=NOVELAI_PROMPT_COMMAND,
                ),
                _command_row(
                    button_id="novelai_image",
                    label=NOVELAI_IMAGE_COMMAND,
                    command=NOVELAI_IMAGE_COMMAND,
                ),
            ]
        }
    }


def novelai_prompt_options_keyboard(
    prompts: tuple[str, str, str],
    token: str,
    *,
    full_reply_url: str | None = None,
) -> dict[str, Any]:
    rows = [
        _command_row(
            button_id=f"novelai_prompt_option_{index}",
            label=f"使用方案{index}",
            command=f"{NOVELAI_IMAGE_COMMAND} {prompt}",
        )
        for index, prompt in enumerate(prompts, 1)
    ]
    rows.append(
        _command_row(
            button_id="revise_novelai_prompt",
            label="修改提示词",
            command=f"{NOVELAI_PROMPT_EDIT_COMMAND} {token} ",
        )
    )
    if full_reply_url:
        rows.append(_link_row(full_reply_url))
    return {"content": {"rows": rows}}


def _command_row(
    *, button_id: str, label: str, command: str, enter: bool = False
) -> dict[str, Any]:
    return {
        "buttons": [
            {
                "id": button_id,
                "render_data": {
                    "label": label,
                    "visited_label": label,
                    "style": 1,
                },
                "action": {
                    "type": 2,
                    "permission": {"type": 2},
                    "data": command,
                    "enter": enter,
                },
            }
        ]
    }


def _link_row(url: str) -> dict[str, Any]:
    return {
        "buttons": [
            {
                "id": "view_full_reply",
                "render_data": {
                    "label": "查看完整输出",
                    "visited_label": "查看完整输出",
                    "style": 1,
                },
                "action": {
                    "type": 0,
                    "permission": {"type": 2},
                    "data": url,
                },
            }
        ]
    }
