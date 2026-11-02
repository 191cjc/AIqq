"""Content-free terminal metrics for logical AI turns."""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Mapping
from typing import Any, Literal

from aiqq.logic.ports import AITurnPurpose


logger = logging.getLogger(__name__)
UNKNOWN = "unknown"
TurnStatus = Literal["success", "failure", "cancelled"]
COUNTER_NAMES = (
    "provider_requests",
    "tool_calls",
    "history_pages",
    "image_reads",
    "search_requests",
)


class AITurnMetric:
    def __init__(
        self,
        *,
        purpose: AITurnPurpose,
        backend: str,
        model: str,
        reasoning_effort: str = "none",
        model_input: str,
        output_limit_mode: str,
    ) -> None:
        self._request_id = secrets.token_hex(12)
        self._purpose = purpose
        self._backend = backend
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._input_bytes = len(model_input.encode("utf-8"))
        self._output_limit_mode = output_limit_mode
        self._started = time.monotonic()
        self._finished = False

    def finish(
        self,
        status: TurnStatus,
        *,
        usage: Mapping[str, Any] | None = None,
        counters: Mapping[str, int | str] | None = None,
        error_type: str = "none",
    ) -> None:
        if self._finished:
            return
        self._finished = True
        tokens = normalize_usage(usage)
        observed = {
            name: (counters or {}).get(name, UNKNOWN)
            for name in COUNTER_NAMES
        }
        logger.info(
            "event=ai_turn_finished request_id=%s purpose=%s backend=%s model=%s "
            "reasoning_effort=%s application_input_bytes=%s elapsed_ms=%s status=%s "
            "input_tokens=%s cached_input_tokens=%s output_tokens=%s "
            "provider_requests=%s tool_calls=%s history_pages=%s image_reads=%s "
            "search_requests=%s output_limit_mode=%s error_type=%s",
            self._request_id,
            self._purpose,
            self._backend,
            self._model,
            self._reasoning_effort,
            self._input_bytes,
            max(0, round((time.monotonic() - self._started) * 1000)),
            status,
            tokens["input_tokens"],
            tokens["cached_input_tokens"],
            tokens["output_tokens"],
            observed["provider_requests"],
            observed["tool_calls"],
            observed["history_pages"],
            observed["image_reads"],
            observed["search_requests"],
            self._output_limit_mode,
            error_type,
        )


def normalize_usage(usage: Mapping[str, Any] | None) -> dict[str, int | str]:
    values: dict[str, int | str] = {
        "input_tokens": UNKNOWN,
        "cached_input_tokens": UNKNOWN,
        "output_tokens": UNKNOWN,
    }
    if not isinstance(usage, Mapping):
        return values
    for name in ("input_tokens", "cached_input_tokens", "output_tokens"):
        value = usage.get(name)
        if type(value) is int and value >= 0:
            values[name] = value
    details = usage.get("input_tokens_details")
    if isinstance(details, Mapping):
        cached = details.get("cached_tokens")
        if type(cached) is int and cached >= 0:
            values["cached_input_tokens"] = cached
    return values
