import math
from typing import Any

from commands import (
    CLEAR_MEMORY_COMMAND,
    GPT_IMAGE_COMMAND,
    MENU_COMMAND,
    NOVELAI_IMAGE_COMMAND,
    NOVELAI_PROMPT_EDIT_COMMAND,
    NOVELAI_PROMPT_COMMAND,
)


DEFAULT_MESSAGE_CHARS = 3000


def markdown_reply_chunks(
    content: str,
    max_chars: int = DEFAULT_MESSAGE_CHARS,
    *,
    max_parts: int | None = None,
) -> tuple[str, ...]:
    return split_message_content(
        content,
        max_chars,
        max_parts=max_parts,
    )


def split_message_content(
    content: str,
    max_chars: int = DEFAULT_MESSAGE_CHARS,
    *,
    max_parts: int | None = None,
) -> tuple[str, ...]:
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    if max_parts is not None and max_parts < 1:
        raise ValueError("max_parts must be positive")

    remaining = content.strip()
    if not remaining:
        return ("",)
    if max_parts is not None:
        max_chars = max(max_chars, math.ceil(len(remaining) / max_parts))

    parts: list[str] = []
    while len(remaining) > max_chars:
        parts_left = None if max_parts is None else max_parts - len(parts)
        minimum_cut = max_chars // 2
        if parts_left is not None:
            minimum_cut = max(
                minimum_cut,
                len(remaining) - (parts_left - 1) * max_chars,
            )

        cut = 0
        for separator in ("\n\n", "\n", "。", "！", "？", "；", " "):
            position = remaining.rfind(separator, minimum_cut, max_chars + 1)
            if position >= 0:
                cut = position + len(separator)
                break
        if cut <= 0:
            cut = max_chars

        parts.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()

    parts.append(remaining)
    return tuple(parts)


def _full_reply_button_row(full_reply_url: str) -> dict[str, Any]:
    return {
        "buttons": [
            {
                "id": "copy_full_reply",
                "render_data": {
                    "label": "查看完整输出",
                    "visited_label": "查看完整输出",
                    "style": 1,
                },
                "action": {
                    "type": 0,
                    "permission": {"type": 2},
                    "data": full_reply_url,
                },
            }
        ]
    }


def _image_suggestion_button_row(
    command: str,
    suggestion: str,
    *,
    button_id: str,
) -> dict[str, Any]:
    safe_suggestion = " ".join(suggestion.split()).strip()[:500]
    return {
        "buttons": [
            {
                "id": button_id,
                "render_data": {
                    "label": "🟢 使用建议",
                    "visited_label": "🟢 使用建议",
                    "style": 0,
                },
                "action": {
                    "type": 2,
                    "permission": {"type": 2},
                    "data": f"{command} {safe_suggestion}",
                    "enter": False,
                },
            }
        ]
    }


def quick_menu_keyboard(
    *,
    novelai_suggestion: str | None = None,
    gpt_suggestion: str | None = None,
    novelai_prompt_request: str | None = None,
    full_reply_url: str | None = None,
) -> dict[str, Any]:
    suggestion = (novelai_suggestion or "").strip()[:500]
    safe_gpt_suggestion = (gpt_suggestion or "").strip()[:500]
    prompt_request = " ".join((novelai_prompt_request or "").split())[:500]
    action_data = f"/{MENU_COMMAND}"
    if prompt_request:
        action_data += f" {NOVELAI_PROMPT_COMMAND} {prompt_request}"
    rows = [
        {
            "buttons": [
                {
                    "id": "open_feature_menu",
                    "render_data": {
                        "label": "功能菜单",
                        "visited_label": "功能菜单",
                        "style": 0,
                    },
                    "action": {
                        "type": 2,
                        "permission": {"type": 2},
                        "data": action_data,
                        "enter": True,
                    },
                }
            ]
        }
    ]
    if suggestion:
        rows.append(
            _image_suggestion_button_row(
                NOVELAI_IMAGE_COMMAND,
                suggestion,
                button_id="use_novelai_suggestion",
            )
        )
    if safe_gpt_suggestion:
        rows.append(
            _image_suggestion_button_row(
                GPT_IMAGE_COMMAND,
                safe_gpt_suggestion,
                button_id="use_gpt_suggestion",
            )
        )
    if full_reply_url:
        rows.append(_full_reply_button_row(full_reply_url))
    return {"content": {"rows": rows}}


def novelai_prompt_options_keyboard(
    prompts: tuple[str, ...],
    token: str,
    *,
    full_reply_url: str | None = None,
) -> dict[str, Any]:
    option_rows = []
    for index, prompt in enumerate(prompts, 1):
        safe_prompt = " ".join(prompt.split()).strip()[:500]
        option_rows.append(
            {
                "buttons": [
                    {
                        "id": f"use_generated_novelai_prompt_{index}",
                        "render_data": {
                            "label": f"🟢 使用方案{index}",
                            "visited_label": f"🟢 使用方案{index}",
                            "style": 0,
                        },
                        "action": {
                            "type": 2,
                            "permission": {"type": 2},
                            "data": f"{NOVELAI_IMAGE_COMMAND} {safe_prompt}",
                            "enter": False,
                        },
                    }
                ]
            }
        )
    rows = [
        *option_rows,
        {
            "buttons": [
                {
                    "id": "revise_generated_novelai_prompts",
                    "render_data": {
                        "label": "修改提示词",
                        "visited_label": "修改提示词",
                        "style": 1,
                    },
                    "action": {
                        "type": 2,
                        "permission": {"type": 2},
                        "data": f"{NOVELAI_PROMPT_EDIT_COMMAND} {token} ",
                        "enter": False,
                    },
                }
            ]
        },
    ]
    if full_reply_url:
        rows.append(_full_reply_button_row(full_reply_url))
    return {"content": {"rows": rows}}


def feature_menu_keyboard(
    *,
    novelai_suggestion: str | None = None,
    novelai_prompt_request: str | None = None,
    full_reply_url: str | None = None,
) -> dict[str, Any]:
    rows = [
        {
            "buttons": [
                {
                    "id": "clear_memory",
                    "render_data": {
                        "label": CLEAR_MEMORY_COMMAND,
                        "visited_label": CLEAR_MEMORY_COMMAND,
                        "style": 1,
                    },
                    "action": {
                        "type": 2,
                        "permission": {"type": 2},
                        "data": CLEAR_MEMORY_COMMAND,
                        "enter": False,
                    },
                }
            ]
        },
        {
            "buttons": [
                {
                    "id": "novelai_prompt",
                    "render_data": {
                        "label": NOVELAI_PROMPT_COMMAND,
                        "visited_label": NOVELAI_PROMPT_COMMAND,
                        "style": 1,
                    },
                    "action": {
                        "type": 2,
                        "permission": {"type": 2},
                        "data": NOVELAI_PROMPT_COMMAND,
                        "enter": False,
                    },
                }
            ]
        },
        {
            "buttons": [
                {
                    "id": "novelai_image",
                    "render_data": {
                        "label": NOVELAI_IMAGE_COMMAND,
                        "visited_label": NOVELAI_IMAGE_COMMAND,
                        "style": 1,
                    },
                    "action": {
                        "type": 2,
                        "permission": {"type": 2},
                        "data": NOVELAI_IMAGE_COMMAND,
                        "enter": False,
                    },
                }
            ]
        },
        {
            "buttons": [
                {
                    "id": "gpt_image",
                    "render_data": {
                        "label": GPT_IMAGE_COMMAND,
                        "visited_label": GPT_IMAGE_COMMAND,
                        "style": 1,
                    },
                    "action": {
                        "type": 2,
                        "permission": {"type": 2},
                        "data": GPT_IMAGE_COMMAND,
                        "enter": False,
                    },
                }
            ]
        },
    ]
    prompt_request = " ".join((novelai_prompt_request or "").split())[:500]
    if prompt_request:
        rows.append(
            {
                "buttons": [
                    {
                        "id": "create_novelai_prompt_options",
                        "render_data": {
                            "label": "🟢 生成提示词方案",
                            "visited_label": "🟢 生成提示词方案",
                            "style": 0,
                        },
                        "action": {
                            "type": 2,
                            "permission": {"type": 2},
                            "data": f"{NOVELAI_PROMPT_COMMAND} {prompt_request}",
                            "enter": False,
                        },
                    }
                ]
            }
        )
    elif novelai_suggestion:
        suggestion = novelai_suggestion.strip()
        if suggestion:
            rows.append(
                {
                    "buttons": [
                        {
                            "id": "use_novelai_suggestion",
                            "render_data": {
                                "label": "🟢 使用建议",
                                "visited_label": "🟢 使用建议",
                                "style": 0,
                            },
                            "action": {
                                "type": 2,
                                "permission": {"type": 2},
                                "data": f"{NOVELAI_IMAGE_COMMAND} {suggestion}",
                                "enter": False,
                            },
                        }
                    ]
                }
            )
    if full_reply_url:
        rows.append(_full_reply_button_row(full_reply_url))
    return {"content": {"rows": rows}}


async def reply_with_quick_menu(
    message,
    content: str,
    *,
    msg_seq: int = 1,
    novelai_suggestion: str | None = None,
    gpt_suggestion: str | None = None,
    novelai_prompt_request: str | None = None,
    full_reply_url: str | None = None,
    mention_user_openid: str | None = None,
    max_chars: int = DEFAULT_MESSAGE_CHARS,
    max_parts: int | None = None,
    split_content: bool = True,
):
    if not split_content:
        max_chars = max(max_chars, len(content))
        max_parts = None
    chunks = markdown_reply_chunks(
        content,
        max_chars,
        max_parts=max_parts,
    )
    response = None
    for index, chunk in enumerate(chunks):
        markdown_content = chunk
        if index == 0 and mention_user_openid:
            markdown_content = f"<@{mention_user_openid}> {chunk}"
        reply: dict[str, Any] = {
            "msg_type": 2,
            "markdown": {"content": markdown_content},
            "msg_seq": msg_seq + index,
        }
        if index == len(chunks) - 1:
            reply["keyboard"] = quick_menu_keyboard(
                novelai_suggestion=novelai_suggestion,
                gpt_suggestion=gpt_suggestion,
                novelai_prompt_request=novelai_prompt_request,
                full_reply_url=full_reply_url,
            )
        response = await message.reply(**reply)
    return response


async def reply_with_novelai_prompt_options(
    message,
    content: str,
    prompts: tuple[str, ...],
    token: str,
    *,
    msg_seq: int = 1,
    max_chars: int = DEFAULT_MESSAGE_CHARS,
    full_reply_url: str | None = None,
):
    chunks = markdown_reply_chunks(content, max_chars)
    response = None
    for index, chunk in enumerate(chunks):
        reply: dict[str, Any] = {
            "msg_type": 2,
            "markdown": {"content": chunk},
            "msg_seq": msg_seq + index,
        }
        if index == len(chunks) - 1:
            reply["keyboard"] = novelai_prompt_options_keyboard(
                prompts,
                token,
                full_reply_url=full_reply_url,
            )
        response = await message.reply(**reply)
    return response


async def reply_feature_menu(
    message,
    *,
    content: str = "请选择要使用的功能。",
    novelai_suggestion: str | None = None,
    novelai_prompt_request: str | None = None,
    full_reply_url: str | None = None,
    msg_seq: int = 1,
):
    return await message.reply(
        msg_type=2,
        markdown={"content": content},
        keyboard=feature_menu_keyboard(
            novelai_suggestion=novelai_suggestion,
            novelai_prompt_request=novelai_prompt_request,
            full_reply_url=full_reply_url,
        ),
        msg_seq=msg_seq,
    )


async def reply_research_stage(message, content: str, *, msg_seq: int):
    chunks = markdown_reply_chunks(content)
    return await message.reply(
        msg_type=2,
        markdown={"content": chunks[0]},
        msg_seq=msg_seq,
    )
