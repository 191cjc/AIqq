from typing import Any

from commands import CLEAR_MEMORY_COMMAND, NOVELAI_IMAGE_COMMAND


def clear_memory_keyboard(
    *, novelai_suggestion: str | None = None
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
    ]
    if novelai_suggestion:
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
    return {"content": {"rows": rows}}


async def reply_with_clear_memory_button(
    message,
    content: str,
    *,
    msg_seq: int = 1,
    novelai_suggestion: str | None = None,
):
    return await message.reply(
        msg_type=2,
        markdown={"content": content},
        keyboard=clear_memory_keyboard(novelai_suggestion=novelai_suggestion),
        msg_seq=msg_seq,
    )


async def reply_research_stage(message, content: str, *, msg_seq: int):
    return await message.reply(
        msg_type=2,
        markdown={"content": content},
        msg_seq=msg_seq,
    )
