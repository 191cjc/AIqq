"""Parse and aggregate privacy-safe group-context preparation events."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo


EVENT_NAME = "group_history_context_prepared"
TIMESTAMP_RE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:,\d+|\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)")
FIELD_RE = re.compile(r"(?:^|\s)([a-z_]+)=([^\s]+)")
INTEGER_FIELDS = {
    "candidate_message_count",
    "included_message_count",
    "source_char_count",
    "included_char_count",
    "dropped_message_count",
    "dropped_char_count",
    "char_limit",
}


@dataclass(frozen=True)
class HistoryMetricEvent:
    timestamp: datetime
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
class HistoryMetricSummary:
    prepared_context_count: int
    truncated_context_count: int
    truncation_rate: float
    message_limit_reached_count: int
    average_source_char_count: float
    maximum_source_char_count: int
    average_dropped_char_count: float


def parse_history_metric_line(
    line: str, *, default_timezone: tzinfo | None = None
) -> HistoryMetricEvent | None:
    if f"event={EVENT_NAME}" not in line:
        return None
    match = TIMESTAMP_RE.search(line)
    if match is None:
        return None
    timestamp = _parse_timestamp(match.group(1), default_timezone)
    if timestamp is None:
        return None
    fields = dict(FIELD_RE.findall(line))
    try:
        integers = {name: int(fields[name]) for name in INTEGER_FIELDS}
        truncated = _boolean(fields["truncated"])
        message_limit_reached = _boolean(fields["message_limit_reached"])
    except (KeyError, ValueError):
        return None
    if any(value < 0 for value in integers.values()) or integers["char_limit"] < 1:
        return None
    return HistoryMetricEvent(
        timestamp=timestamp,
        truncated=truncated,
        message_limit_reached=message_limit_reached,
        **integers,
    )


def summarize_history_metrics(
    events: tuple[HistoryMetricEvent, ...],
    *,
    start: datetime,
    end: datetime,
) -> HistoryMetricSummary:
    selected = tuple(event for event in events if start <= event.timestamp < end)
    count = len(selected)
    truncated = sum(event.truncated for event in selected)
    reached_limit = sum(event.message_limit_reached for event in selected)
    source_total = sum(event.source_char_count for event in selected)
    dropped_total = sum(event.dropped_char_count for event in selected)
    return HistoryMetricSummary(
        prepared_context_count=count,
        truncated_context_count=truncated,
        truncation_rate=truncated / count if count else 0.0,
        message_limit_reached_count=reached_limit,
        average_source_char_count=source_total / count if count else 0.0,
        maximum_source_char_count=max(
            (event.source_char_count for event in selected), default=0
        ),
        average_dropped_char_count=dropped_total / count if count else 0.0,
    )


def reporting_windows(
    report_date: date, local_timezone: tzinfo
) -> tuple[tuple[str, datetime, datetime], ...]:
    today_start = datetime.combine(
        report_date, datetime.min.time(), tzinfo=local_timezone
    )
    tomorrow = today_start + timedelta(days=1)
    return (
        ("today", today_start, tomorrow),
        ("last_7_days", today_start - timedelta(days=6), tomorrow),
    )


def _parse_timestamp(value: str, default_timezone: tzinfo | None) -> datetime | None:
    normalized = value.replace(",", ".").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=default_timezone or datetime.now().astimezone().tzinfo or timezone.utc
        )
    return parsed


def _boolean(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError("invalid boolean")
