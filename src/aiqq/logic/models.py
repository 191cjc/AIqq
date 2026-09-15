"""Framework-independent business objects shared across application layers."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


PromptAuditCategory = Literal[
    "safe",
    "adult_content",
    "persona_override",
    "complex_research",
    "too_many_questions",
    "too_complex",
]
ImageActionMode = Literal["generate", "edit"]
ConversationStatus = Literal["ok", "rejected", "unavailable"]
ImageUsageDenialReason = Literal["busy", "daily_limit"]


@dataclass(frozen=True)
class SourceReference:
    title: str
    url: str

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("source title must not be empty")
        if not self.url.strip():
            raise ValueError("source URL must not be empty")


@dataclass(frozen=True)
class ImageAsset:
    data: bytes
    mime_type: str
    width: int
    height: int

    def __post_init__(self) -> None:
        if not self.data:
            raise ValueError("image data must not be empty")
        if self.mime_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError("unsupported image MIME type")
        if self.width < 1 or self.height < 1:
            raise ValueError("image dimensions must be positive")


@dataclass(frozen=True)
class ImageAction:
    mode: ImageActionMode
    prompt: str
    source_record_id: int | None = None

    def __post_init__(self) -> None:
        if not self.prompt.strip() or len(self.prompt) > 2000:
            raise ValueError("image prompt must contain 1 to 2000 characters")
        if self.mode == "generate" and self.source_record_id is not None:
            raise ValueError("generate action cannot have a source record")
        if self.mode == "edit" and (
            self.source_record_id is None or self.source_record_id < 1
        ):
            raise ValueError("edit action requires a positive source record ID")


@dataclass(frozen=True)
class GroupHistoryMessage:
    record_id: int
    role: Literal["user", "assistant"]
    sender_name: str
    sent_at: str
    content: str
    message_type: int | None = None
    reply_summary: str = ""
    has_image: bool = False

    def __post_init__(self) -> None:
        if self.record_id < 1:
            raise ValueError("history record ID must be positive")
        if not self.sent_at:
            raise ValueError("history timestamp must not be empty")


@dataclass(frozen=True)
class ConversationRequest:
    user_input: str
    group_history: tuple[GroupHistoryMessage, ...]
    reference_material_available: bool
    conversation_key: str

    def __post_init__(self) -> None:
        if not self.user_input.strip():
            raise ValueError("user input must not be empty")
        if not self.conversation_key:
            raise ValueError("conversation key must not be empty")
        if not self.reference_material_available and self.group_history:
            raise ValueError("unavailable reference material must be empty")


@dataclass(frozen=True)
class PromptAuditResult:
    allowed: bool
    category: PromptAuditCategory
    reason: str

    def __post_init__(self) -> None:
        if self.allowed and self.category != "safe":
            raise ValueError("allowed audit result must use safe category")
        if not self.allowed and self.category == "safe":
            raise ValueError("rejected audit result cannot use safe category")
        if not self.allowed and not self.reason.strip():
            raise ValueError("rejected audit result requires a reason")


@dataclass(frozen=True)
class ImagePromptAuditResult:
    safe: bool
    effective: bool
    category: Literal["safe", "adult_content"]
    reason: str
    suggested_prompt: str
    orientation: Literal["square", "portrait", "landscape"]

    def __post_init__(self) -> None:
        if self.safe != (self.category == "safe"):
            raise ValueError("image audit safety and category disagree")
        if not self.reason.strip():
            raise ValueError("image audit result requires a reason")
        if (not self.safe or not self.effective) and not self.suggested_prompt.strip():
            raise ValueError("rejected image prompt requires a safe suggestion")


@dataclass(frozen=True)
class NovelAIPromptOptions:
    prompts: tuple[str, str, str]

    def __post_init__(self) -> None:
        if len(self.prompts) != 3:
            raise ValueError("exactly three NovelAI prompts are required")
        normalized = tuple(prompt.strip() for prompt in self.prompts)
        if any(not prompt or len(prompt) > 500 for prompt in normalized):
            raise ValueError("NovelAI prompts must contain 1 to 500 characters")
        if any("\n" in prompt or "\r" in prompt for prompt in normalized):
            raise ValueError("NovelAI prompts must be single-line values")
        if len(set(normalized)) != 3:
            raise ValueError("NovelAI prompt options must be distinct")


@dataclass(frozen=True)
class NovelAIPromptSession:
    token: str
    options: NovelAIPromptOptions

    def __post_init__(self) -> None:
        if not self.token or len(self.token) > 64:
            raise ValueError("NovelAI prompt session token is invalid")


@dataclass(frozen=True)
class ImageUsageDecision:
    allowed: bool
    reason: ImageUsageDenialReason | None = None
    daily_limit: int | None = None

    def __post_init__(self) -> None:
        if self.allowed and (self.reason is not None or self.daily_limit is not None):
            raise ValueError("allowed image usage cannot include a denial reason")
        if not self.allowed and self.reason is None:
            raise ValueError("denied image usage requires a reason")
        if self.reason == "daily_limit" and (
            self.daily_limit is None or self.daily_limit < 1
        ):
            raise ValueError("daily quota denial requires a positive limit")


@dataclass(frozen=True)
class ChatAgentOutput:
    full_text: str
    summary: str
    image_action: ImageAction | None = None
    generated_images: tuple[ImageAsset, ...] = ()
    selected_web_image_url: str | None = None
    sources: tuple[SourceReference, ...] = ()

    def __post_init__(self) -> None:
        if not self.full_text.strip():
            raise ValueError("chat output must include full text")
        if not 1 <= len(self.summary.strip()) <= 50:
            raise ValueError("chat summary must contain 1 to 50 characters")
        if len(self.generated_images) > 1:
            raise ValueError("chat output supports at most one generated image")
        if len(self.sources) > 3:
            raise ValueError("chat output supports at most three sources")


@dataclass(frozen=True)
class ConversationResult:
    status: ConversationStatus
    full_text: str
    summary: str
    images: tuple[ImageAsset, ...] = ()
    sources: tuple[SourceReference, ...] = ()
    error_code: str | None = None

    def __post_init__(self) -> None:
        if not self.full_text.strip():
            raise ValueError("conversation result must include display text")
        if not 1 <= len(self.summary.strip()) <= 50:
            raise ValueError("conversation summary must contain 1 to 50 characters")
        if len(self.images) > 1:
            raise ValueError("conversation result supports at most one image")
        if len(self.sources) > 3:
            raise ValueError("conversation result supports at most three sources")
        if self.status == "ok" and self.error_code is not None:
            raise ValueError("successful result cannot include an error code")
        if self.status != "ok" and self.images:
            raise ValueError("failed or rejected result cannot include images")
        if self.status == "unavailable" and not self.error_code:
            raise ValueError("unavailable result requires an error code")


@dataclass(frozen=True)
class NovelAIPromptWorkflowResult:
    result: ConversationResult
    session: NovelAIPromptSession | None = None

    def __post_init__(self) -> None:
        if self.result.status == "ok" and self.session is None:
            raise ValueError("successful NovelAI prompt workflow requires a session")
        if self.result.status != "ok" and self.session is not None:
            raise ValueError("failed NovelAI prompt workflow cannot include a session")


@dataclass(frozen=True)
class BotSentGroupMessage:
    group_id: str
    message_id: str
    sent_at: datetime
    recorded: bool

    def __post_init__(self) -> None:
        if not self.group_id or not self.message_id:
            raise ValueError("sent group message requires group and message IDs")


@dataclass(frozen=True)
class GroupReplyContext:
    group_id: str
    message_id: str
    next_sequence: int
    expires_at: datetime

    def __post_init__(self) -> None:
        if not self.group_id or not self.message_id:
            raise ValueError("reply context requires group and message IDs")
        if not 1 <= self.next_sequence <= 5:
            raise ValueError("reply sequence must be between 1 and 5")
        if self.expires_at.tzinfo is None:
            raise ValueError("reply context expiration must be timezone-aware")
