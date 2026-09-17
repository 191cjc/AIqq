"""Application-level exception types."""


class AiQQError(Exception):
    """Base class for expected AiQQ failures."""


class ConfigurationError(AiQQError):
    """Raised when application configuration is invalid."""


class AgentError(AiQQError):
    """Base class for AI agent failures."""


class AgentUnavailable(AgentError):
    """Raised when an AI backend cannot complete a request."""


class AgentContextTooLarge(AgentUnavailable):
    """The complete supplied history exceeds the model's actual context limit."""


class InvalidAgentOutput(AgentError):
    """Raised when model output violates its structured contract."""

    def __init__(self, agent: str, detail: str):
        super().__init__(f"{agent} returned invalid structured output: {detail}")
        self.agent = agent
        self.detail = detail


class PromptAuditUnavailable(AgentUnavailable):
    """Raised when prompt auditing cannot produce a valid decision."""


class ConversationUnavailable(AgentUnavailable):
    """Raised when the chat agent cannot produce a valid result."""


class ImageAuditUnavailable(AgentUnavailable):
    """Raised when image prompt auditing cannot produce a valid decision."""


class ImageGenerationUnavailable(AiQQError):
    """Expected image failure with safe metadata for workflow error handling."""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "unknown",
        attempt_id: str = "",
        status_code: int | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.attempt_id = attempt_id
        self.status_code = status_code
        self.provider_request_id = provider_request_id


class ReferenceImageUnavailable(AiQQError):
    """No reference image could be loaded; contains only safe failure metadata."""

    def __init__(
        self,
        message: str = "referenced image is unavailable",
        *,
        kind: str = "download_failed",
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind if kind in {
            "expired", "missing", "timeout", "access_denied",
            "invalid_image", "too_large", "download_failed",
        } else "download_failed"
        self.status_code = (
            status_code
            if type(status_code) is int and 100 <= status_code <= 599
            else None
        )


class NovelAIPromptUnavailable(AgentUnavailable):
    """Raised when NovelAI prompt generation cannot produce valid options."""
