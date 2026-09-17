"""Safe classifications shared by the model transports."""
from __future__ import annotations


def is_context_limit_error(value: object) -> bool:
    message = str(value).lower()
    return any(marker in message for marker in (
        "context_length_exceeded", "context_window_exceeded", "context_length_error",
        "maximum context length", "exceeds the context window",
        "exceeded the model's context window", "input exceeds the context window",
    ))
