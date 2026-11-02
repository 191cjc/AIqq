"""Per-member GPT usage lookup with an admin overview."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .models import ConversationResult


NOT_A_USER_TEXT = "不在用户中，无法查询 GPT 用量。"


class GPTUsageUnavailable(Exception):
    """The usage endpoint could not be read for one key."""


@dataclass(frozen=True)
class GPTUsageAccount:
    member_openid: str
    label: str
    api_key: str


class GPTUsageFetcher(Protocol):
    async def fetch(self, api_key: str) -> Mapping[str, Any]: ...


class GPTUsageWorkflow:
    def __init__(
        self,
        *,
        fetcher: GPTUsageFetcher,
        accounts: Iterable[GPTUsageAccount],
        admin_member_ids: Iterable[str] = (),
    ) -> None:
        self._fetcher = fetcher
        self._accounts = tuple(accounts)
        self._by_member = {account.member_openid: account for account in self._accounts}
        self._admins = frozenset(admin_member_ids)

    async def run(self, *, member_openid: str) -> ConversationResult:
        if member_openid and member_openid in self._admins:
            return await self._admin_overview()
        account = self._by_member.get(member_openid) if member_openid else None
        if account is None:
            return ConversationResult(
                "rejected", NOT_A_USER_TEXT, NOT_A_USER_TEXT,
                error_code="gpt_usage_not_a_user",
            )
        try:
            payload = await self._fetcher.fetch(account.api_key)
        except GPTUsageUnavailable:
            text = "GPT 用量查询失败，请稍后再试。"
            return ConversationResult(
                "unavailable", text, text, error_code="gpt_usage_unavailable"
            )
        return ConversationResult(
            "ok", format_usage(account.label, payload), _summary(account.label, payload)
        )

    async def _admin_overview(self) -> ConversationResult:
        if not self._accounts:
            text = "尚未配置 GPT 用量映射。"
            return ConversationResult("ok", text, text)
        results = await asyncio.gather(
            *(self._fetcher.fetch(account.api_key) for account in self._accounts),
            return_exceptions=True,
        )
        sections = []
        brief = []
        for account, payload in zip(self._accounts, results):
            if isinstance(payload, BaseException):
                if not isinstance(payload, GPTUsageUnavailable):
                    raise payload
                sections.append(f"**{account.label}**\n查询失败，请稍后再试。")
                brief.append(f"{account.label}查询失败")
            else:
                sections.append(format_usage(account.label, payload))
                total = _period_cost(payload, "total")
                brief.append(
                    f"{account.label}{CURRENCY_SIGN}{total:.4f}"
                    if total is not None else f"{account.label}无数据"
                )
        summary = "累计已消耗：" + "；".join(brief)
        if len(summary) > 50:
            summary = f"已查询 {len(self._accounts)} 位用户的 GPT 用量，详见完整输出。"
        return ConversationResult("ok", "\n\n".join(sections), summary)


# The relay reports a USD figure; display it with the RMB sign as-is.
CURRENCY_SIGN = "¥"


def format_usage(label: str, payload: Mapping[str, Any]) -> str:
    lines = [f"**{label}**"]
    for title, key in (("累计已消耗", "total"), ("今日已消耗", "today")):
        cost = _period_cost(payload, key)
        if cost is not None:
            lines.append(f"{title}：{CURRENCY_SIGN}{cost:.4f}")
    if len(lines) == 1:
        lines.append("暂无用量数据。")
    return "\n".join(lines)


def _summary(label: str, payload: Mapping[str, Any]) -> str:
    parts = []
    for title, key in (("累计已消耗", "total"), ("今日", "today")):
        cost = _period_cost(payload, key)
        if cost is not None:
            parts.append(f"{title}{CURRENCY_SIGN}{cost:.4f}")
    text = f"{label}：{'，'.join(parts) or '暂无用量数据'}"
    return text[:50]


def _period_cost(payload: Mapping[str, Any], key: str) -> float | None:
    """Charged amount (``actual_cost``) for ``usage.total`` / ``usage.today``."""
    usage = payload.get("usage")
    period = usage.get(key) if isinstance(usage, Mapping) else None
    if not isinstance(period, Mapping):
        return None
    actual = _number(period.get("actual_cost"))
    return actual if actual is not None else _number(period.get("cost"))


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
