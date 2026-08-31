CLEAR_MEMORY_COMMAND = "清除记忆"
NOVELAI_IMAGE_COMMAND = "NovelAI生图"
MEMORY_CLEAR_COMMANDS = frozenset(
    {
        CLEAR_MEMORY_COMMAND,
        f"/{CLEAR_MEMORY_COMMAND}",
        "新对话",
        "/新对话",
    }
)


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
