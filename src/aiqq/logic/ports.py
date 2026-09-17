"""Protocols owned by the logic layer."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .chat_read import ChatReadSession

from .models import (
    BotSentGroupMessage,
    ChatAgentOutput,
    ConversationRequest,
    GroupReplyContext,
    GroupHistoryMessage,
    ImageAction,
    ImageAsset,
    ImagePromptAuditResult,
    ImageUsageDecision,
    NovelAIPromptOptions,
    NovelAIPromptSession,
    PromptAuditResult,
)

ProgressCallback = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class AgentTurnResult:
    text: str
    images: tuple[ImageAsset, ...] = ()
    usage: Mapping[str, Any] | None = None
    image_error: str | None = None


class AIBackend(Protocol):
    async def run(
        self,
        *,
        instructions: str,
        model_input: str,
        output_schema: Mapping[str, Any],
        enable_web_search: bool = False,
        enable_gpt_image_skill: bool = False,
        on_progress: ProgressCallback | None = None,
        input_images: Sequence[ImageAsset] = (),
        chat_read_session: "ChatReadSession | None" = None,
    ) -> AgentTurnResult: ...


class PromptAuditor(Protocol):
    async def audit(self, user_input: str) -> PromptAuditResult: ...


class ConversationAgent(Protocol):
    async def run(
        self,
        request: ConversationRequest,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> ChatAgentOutput: ...


class GroupHistoryRepository(Protocol):
    async def get_current_for_reference(
        self, group_id: str, message_id: str
    ) -> GroupHistoryMessage | None: ...

    async def has_before(self, group_id: str, record_id: int) -> bool: ...

    async def list_for_reference(
        self,
        group_id: str,
        *,
        before_message_id: str,
        limit: int,
    ) -> tuple[GroupHistoryMessage, ...]: ...


class ReferenceImageLoader(Protocol):
    """Load a reference or raise ReferenceImageUnavailable; None means missing."""

    async def load(self, group_id: str, record_id: int) -> ImageAsset | None: ...


class ReferenceImageURLRepository(Protocol):
    async def list_image_urls(
        self, group_id: str, record_id: int
    ) -> tuple[str, ...]: ...


class GeneratedImageService(Protocol):
    async def generate(
        self,
        action: ImageAction,
        *,
        reference_image: ImageAsset | None = None,
    ) -> ImageAsset: ...


class ImagePromptAuditor(Protocol):
    async def audit(
        self, prompt: str, *, require_english: bool = False
    ) -> ImagePromptAuditResult: ...


class ImageUsageLimiter(Protocol):
    async def reserve_attempt(self, conversation_key: str) -> ImageUsageDecision: ...

    async def record_success(self, conversation_key: str) -> None: ...

    async def finish_attempt(self, conversation_key: str) -> None: ...


class ImageUsageStore(Protocol):
    async def success_count(self, conversation_key: str, usage_date: str) -> int: ...

    async def increment_success(self, conversation_key: str, usage_date: str) -> None: ...


class NovelAIImageGenerator(Protocol):
    async def generate(
        self, prompt: str, *, orientation: str = "square"
    ) -> ImageAsset: ...


class NovelAIPromptGenerator(Protocol):
    async def generate(self, description: str) -> NovelAIPromptOptions: ...

    async def revise(
        self, existing: NovelAIPromptOptions, request: str
    ) -> NovelAIPromptOptions: ...


class NovelAIPromptSessionRepository(Protocol):
    async def create(
        self, conversation_key: str, options: NovelAIPromptOptions
    ) -> NovelAIPromptSession: ...

    async def load(
        self, conversation_key: str, token: str
    ) -> NovelAIPromptSession | None: ...


class WebImageService(Protocol):
    async def download(self, url: str) -> ImageAsset: ...


class BotMessageRepository(Protocol):
    async def record_delivery_attempt(
        self, *, group_id: str, source_message_id: str = "",
        operation: str, status: str, parameters: dict[str, Any],
        result: Any = None, error_type: str = "", message_id: str = "",
    ) -> int: ...

    async def add_bot_message(
        self,
        *,
        message_id: str,
        group_openid: str,
        username: str,
        content: str,
        message_type: int,
        sent_at: str,
        payload: dict[str, Any],
        source_message_id: str = "",
        progress: bool = False,
    ) -> bool: ...

    async def is_owned_bot_message(
        self, *, group_id: str, message_id: str
    ) -> bool: ...

    async def mark_recalled(
        self, *, group_id: str, message_id: str, recalled_at: str | None = None
    ) -> bool: ...

    async def find_console_reply_context(
        self,
        group_id: str,
        *,
        message_id: str | None = None,
    ) -> GroupReplyContext | None: ...


class GroupMessageSender(Protocol):
    async def send_proactive_text(
        self,
        *,
        group_id: str,
        content: str,
        origin: str = "console",
    ) -> BotSentGroupMessage: ...

    async def send_text_reply(
        self,
        *,
        group_id: str,
        source_message_id: str,
        content: str,
        msg_seq: int,
        progress: bool = False,
        origin: str = "conversation",
        fallback_member_openid: str = "",
    ) -> BotSentGroupMessage: ...

    async def send_markdown_reply(
        self,
        *,
        group_id: str,
        source_message_id: str,
        content: str,
        msg_seq: int,
        keyboard: Mapping[str, Any] | None = None,
        record_content: str | None = None,
        origin: str = "conversation",
        fallback_member_openid: str = "",
    ) -> BotSentGroupMessage: ...

    async def send_image_reply(
        self,
        *,
        group_id: str,
        source_message_id: str,
        public_url: str,
        mime_type: str,
        msg_seq: int,
        origin: str = "conversation",
        fallback_member_openid: str = "",
        image_metadata: Mapping[str, Any] | None = None,
    ) -> BotSentGroupMessage: ...

    async def recall_from_group(self, target: BotSentGroupMessage) -> bool: ...
