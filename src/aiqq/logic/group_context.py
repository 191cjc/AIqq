"""Normalization, newest-first budgeting, and metrics for group references."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from typing import Any

from .models import GroupHistoryMessage


logger = logging.getLogger(__name__)
TRUNCATION_MARKER = "[truncated]"


@dataclass(frozen=True)
class GroupContextStats:
    candidate_message_count: int
    included_message_count: int
    source_char_count: int
    included_char_count: int
    dropped_message_count: int
    dropped_char_count: int
    truncated: bool
    message_limit_reached: bool
    char_limit: int


@dataclass(frozen=True)
class PreparedGroupContext:
    messages: tuple[GroupHistoryMessage, ...]
    stats: GroupContextStats


def prepare_group_context(
    messages: tuple[GroupHistoryMessage, ...],
    *,
    request_id: str,
    message_limit: int = 50,
    char_limit: int = 1000,
    event_logger: logging.Logger = logger,
) -> PreparedGroupContext:
    if message_limit < 1 or char_limit < 1:
        raise ValueError("group context limits must be positive")
    candidates = tuple(_normalize_message(message) for message in messages[-message_limit:])
    source_char_count = sum(_message_char_count(message) for message in candidates)
    remaining = char_limit
    selected_reversed: list[GroupHistoryMessage] = []

    for message in reversed(candidates):
        content, remaining, content_complete = _fit_text(message.content, remaining)
        reply_summary, remaining, reply_complete = _fit_text(
            message.reply_summary, remaining
        )
        if not content and not reply_summary and _message_char_count(message) > 0:
            continue
        selected_reversed.append(
            replace(message, content=content, reply_summary=reply_summary)
        )
        if not content_complete or not reply_complete or remaining == 0:
            break

    selected = tuple(reversed(selected_reversed))
    included_char_count = sum(_message_char_count(message) for message in selected)
    stats = GroupContextStats(
        candidate_message_count=len(candidates),
        included_message_count=len(selected),
        source_char_count=source_char_count,
        included_char_count=included_char_count,
        dropped_message_count=len(candidates) - len(selected),
        dropped_char_count=max(0, source_char_count - included_char_count),
        truncated=source_char_count > included_char_count,
        message_limit_reached=len(candidates) == message_limit,
        char_limit=char_limit,
    )
    event_logger.info(
        "event=group_history_context_prepared request_id=%s "
        "candidate_message_count=%s included_message_count=%s "
        "source_char_count=%s included_char_count=%s dropped_message_count=%s "
        "dropped_char_count=%s truncated=%s message_limit_reached=%s char_limit=%s",
        request_id,
        stats.candidate_message_count,
        stats.included_message_count,
        stats.source_char_count,
        stats.included_char_count,
        stats.dropped_message_count,
        stats.dropped_char_count,
        str(stats.truncated).lower(),
        str(stats.message_limit_reached).lower(),
        stats.char_limit,
    )
    return PreparedGroupContext(selected, stats)


def _normalize_message(message: GroupHistoryMessage) -> GroupHistoryMessage:
    return replace(
        message,
        sender_name=_normalize_text(message.sender_name),
        content=_normalize_text(message.content),
        reply_summary=_normalize_text(message.reply_summary),
    )


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _message_char_count(message: GroupHistoryMessage) -> int:
    return len(message.content) + len(message.reply_summary)


def _fit_text(value: str, remaining: int) -> tuple[str, int, bool]:
    if not value:
        return "", remaining, True
    if len(value) <= remaining:
        return value, remaining - len(value), True
    if remaining <= len(TRUNCATION_MARKER):
        return "", remaining, False
    prefix_length = remaining - len(TRUNCATION_MARKER)
    fitted = value[:prefix_length].rstrip() + TRUNCATION_MARKER
    return fitted, remaining - len(fitted), False


def context_stats_as_dict(stats: GroupContextStats) -> dict[str, Any]:
    """Stable serialization for a daily/7-day log aggregator."""
    return {
        "candidate_message_count": stats.candidate_message_count,
        "included_message_count": stats.included_message_count,
        "source_char_count": stats.source_char_count,
        "included_char_count": stats.included_char_count,
        "dropped_message_count": stats.dropped_message_count,
        "dropped_char_count": stats.dropped_char_count,
        "truncated": stats.truncated,
        "message_limit_reached": stats.message_limit_reached,
        "char_limit": stats.char_limit,
    }
