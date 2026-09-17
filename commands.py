CLEAR_MEMORY_COMMAND = "清除记忆"
GPT_IMAGE_COMMAND = "GPT生图"
NOVELAI_IMAGE_COMMAND = "NovelAI生图"
NOVELAI_PROMPT_COMMAND = "NovelAI提示词"
NOVELAI_PROMPT_EDIT_COMMAND = "修改NovelAI提示词"
MENU_COMMAND = "菜单"
MENU_COMMANDS = (MENU_COMMAND, f"/{MENU_COMMAND}", "功能菜单", "/功能菜单")
MEMORY_CLEAR_COMMANDS = frozenset(
    {
        CLEAR_MEMORY_COMMAND,
        f"/{CLEAR_MEMORY_COMMAND}",
        "新对话",
        "/新对话",
    }
)


def extract_menu_suggestion(content: str) -> str | None:
    for command in MENU_COMMANDS:
        if content == command:
            return ""
        if not content.startswith(command):
            continue
        suffix = content[len(command) :]
        if suffix and suffix[0].isspace():
            return suffix.strip()[:500]
    return None


def extract_novelai_prompt(content: str) -> str | None:
    for command in (NOVELAI_IMAGE_COMMAND, f"/{NOVELAI_IMAGE_COMMAND}"):
        if content == command:
            return ""
        if not content.startswith(command):
            continue
        suffix = content[len(command) :]
        if suffix and suffix[0].isspace():
            return suffix.strip()
    return None


def extract_gpt_image_prompt(content: str) -> str | None:
    for command in (GPT_IMAGE_COMMAND, f"/{GPT_IMAGE_COMMAND}"):
        if content == command:
            return ""
        if not content.startswith(command):
            continue
        suffix = content[len(command) :]
        if suffix and suffix[0].isspace():
            return suffix.strip()
    return None


def extract_novelai_prompt_request(content: str) -> str | None:
    for command in (NOVELAI_PROMPT_COMMAND, f"/{NOVELAI_PROMPT_COMMAND}"):
        if content == command:
            return ""
        if not content.startswith(command):
            continue
        suffix = content[len(command) :]
        if suffix and suffix[0].isspace():
            return suffix.strip()
    return None


def extract_novelai_prompt_edit_request(
    content: str,
) -> tuple[str, str] | None:
    for command in (
        NOVELAI_PROMPT_EDIT_COMMAND,
        f"/{NOVELAI_PROMPT_EDIT_COMMAND}",
    ):
        if content == command:
            return "", ""
        if not content.startswith(command):
            continue
        suffix = content[len(command) :]
        if not suffix or not suffix[0].isspace():
            continue
        parts = suffix.strip().split(maxsplit=1)
        token = parts[0] if parts else ""
        request = parts[1] if len(parts) > 1 else ""
        return token, request
    return None
