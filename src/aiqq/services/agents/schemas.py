"""Strict model-output schemas and their only supported parsers."""

from __future__ import annotations

import ipaddress
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from aiqq.exceptions import InvalidAgentOutput
from aiqq.logic.models import (
    ChatAgentOutput,
    ImageAction,
    ImagePromptAuditResult,
    NovelAIPromptOptions,
    PromptAuditResult,
    SourceReference,
)


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
SummaryText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=50),
]
PromptText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
]
TitleText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]
UrlText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2048),
]
NovelPromptText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]


class _StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _PromptAuditOutput(_StrictOutput):
    allowed: bool
    category: Literal[
        "safe",
        "adult_content",
        "persona_override",
        "complex_research",
        "too_many_questions",
        "too_complex",
    ]
    reason: str = Field(max_length=300)

    @model_validator(mode="after")
    def validate_decision(self) -> "_PromptAuditOutput":
        if self.allowed and self.category != "safe":
            raise ValueError("allowed output must use the safe category")
        if not self.allowed and self.category == "safe":
            raise ValueError("rejected output cannot use the safe category")
        if not self.allowed and not self.reason.strip():
            raise ValueError("rejected output requires a reason")
        return self


class _ImageActionOutput(_StrictOutput):
    mode: Literal["generate", "edit"]
    prompt: PromptText
    source_record_id: int | None = Field(ge=1)

    @model_validator(mode="after")
    def validate_reference(self) -> "_ImageActionOutput":
        if self.mode == "generate" and self.source_record_id is not None:
            raise ValueError("generate cannot include a source record")
        if self.mode == "edit" and self.source_record_id is None:
            raise ValueError("edit requires a source record")
        return self


class _SourceOutput(_StrictOutput):
    title: TitleText
    url: UrlText

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _validate_public_https_url(value)


class _ChatOutput(_StrictOutput):
    full_text: NonEmptyText
    summary: SummaryText
    image_action: _ImageActionOutput | None
    selected_web_image_url: UrlText | None
    sources: list[_SourceOutput] = Field(max_length=3)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("summary must be a single line")
        if re.search(r"https?://|\[\[aiqq_|[`*_#]", value, re.IGNORECASE):
            raise ValueError("summary cannot contain links, Markdown, or machine markers")
        return value

    @field_validator("selected_web_image_url")
    @classmethod
    def validate_image_url(cls, value: str | None) -> str | None:
        return None if value is None else _validate_public_https_url(value)


class _ImagePromptAuditOutput(_StrictOutput):
    safe: bool
    effective: bool
    category: Literal["safe", "adult_content"]
    reason: NonEmptyText
    suggested_prompt: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
    orientation: Literal["square", "portrait", "landscape"]

    @model_validator(mode="after")
    def validate_decision(self) -> "_ImagePromptAuditOutput":
        if self.safe != (self.category == "safe"):
            raise ValueError("safe and category disagree")
        if (not self.safe or not self.effective) and not self.suggested_prompt:
            raise ValueError("rejected image prompt requires a safe suggestion")
        return self


class _NovelAIPromptOptionsOutput(_StrictOutput):
    prompts: list[NovelPromptText] = Field(min_length=3, max_length=3)

    @field_validator("prompts")
    @classmethod
    def validate_prompts(cls, prompts: list[str]) -> list[str]:
        if any("\n" in prompt or "\r" in prompt for prompt in prompts):
            raise ValueError("prompts must be single-line values")
        if len(set(prompts)) != 3:
            raise ValueError("prompt options must be distinct")
        return prompts


def _schema(model: type[BaseModel]) -> dict[str, object]:
    schema = model.model_json_schema(mode="validation")
    schema.pop("title", None)
    return schema


PROMPT_AUDIT_OUTPUT_SCHEMA = _schema(_PromptAuditOutput)
CHAT_AGENT_OUTPUT_SCHEMA = _schema(_ChatOutput)
IMAGE_PROMPT_AUDIT_OUTPUT_SCHEMA = _schema(_ImagePromptAuditOutput)
NOVELAI_PROMPT_OPTIONS_OUTPUT_SCHEMA = _schema(_NovelAIPromptOptionsOutput)


def _validate_public_https_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL is malformed") from exc
    hostname = parsed.hostname
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        raise ValueError("URL must be a credential-free public HTTPS URL")
    normalized_host = hostname.rstrip(".").lower()
    if normalized_host == "localhost" or normalized_host.endswith(".local"):
        raise ValueError("local URL is forbidden")
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        if "." not in normalized_host:
            raise ValueError("URL hostname must be fully qualified")
    else:
        if not address.is_global:
            raise ValueError("non-public IP address is forbidden")
    return value


def _parse(model: type[_StrictOutput], raw: str, agent: str) -> _StrictOutput:
    if not isinstance(raw, str) or not raw.strip():
        raise InvalidAgentOutput(agent, "empty response")
    try:
        return model.model_validate_json(raw, strict=True)
    except ValidationError as exc:
        detail = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
            for error in exc.errors(include_url=False)[:3]
        )
        raise InvalidAgentOutput(agent, detail) from exc


def parse_prompt_audit_output(raw: str) -> PromptAuditResult:
    value = _parse(_PromptAuditOutput, raw, "prompt_audit")
    assert isinstance(value, _PromptAuditOutput)
    return PromptAuditResult(value.allowed, value.category, value.reason.strip())


def parse_chat_agent_output(
    raw: str,
    *,
    allowed_image_record_ids: frozenset[int] = frozenset(),
) -> ChatAgentOutput:
    value = _parse(_ChatOutput, raw, "chat")
    assert isinstance(value, _ChatOutput)
    image_action = None
    if value.image_action is not None:
        action = value.image_action
        if (
            action.mode == "edit"
            and action.source_record_id not in allowed_image_record_ids
        ):
            raise InvalidAgentOutput(
                "chat", "image_action references an unavailable image record"
            )
        image_action = ImageAction(
            mode=action.mode,
            prompt=action.prompt,
            source_record_id=action.source_record_id,
        )
    return ChatAgentOutput(
        full_text=value.full_text,
        summary=value.summary,
        image_action=image_action,
        selected_web_image_url=value.selected_web_image_url,
        sources=tuple(SourceReference(item.title, item.url) for item in value.sources),
    )


def parse_image_prompt_audit_output(raw: str) -> ImagePromptAuditResult:
    value = _parse(_ImagePromptAuditOutput, raw, "image_prompt_audit")
    assert isinstance(value, _ImagePromptAuditOutput)
    return ImagePromptAuditResult(
        safe=value.safe,
        effective=value.effective,
        category=value.category,
        reason=value.reason,
        suggested_prompt=value.suggested_prompt,
        orientation=value.orientation,
    )


def parse_novelai_prompt_options_output(raw: str) -> NovelAIPromptOptions:
    value = _parse(_NovelAIPromptOptionsOutput, raw, "novelai_prompt")
    assert isinstance(value, _NovelAIPromptOptionsOutput)
    prompts = tuple(value.prompts)
    return NovelAIPromptOptions((prompts[0], prompts[1], prompts[2]))
