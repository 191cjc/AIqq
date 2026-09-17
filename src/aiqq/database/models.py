"""Database record models; these never cross into the logic layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StoredGroupMessage:
    record_id: int
    message_id: str
    event_id: str
    event_type: str
    group_openid: str
    member_openid: str
    username: str
    member_role: str
    is_bot: bool
    content: str
    message_type: int | None
    sent_at: str
    received_at: str
    payload: dict[str, Any]
    recalled_at: str = ""
    record: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GroupMessageSummary:
    group_openid: str
    message_count: int
    latest_record_id: int
    latest_member_openid: str
    latest_username: str
    latest_content: str
    latest_sent_at: str
