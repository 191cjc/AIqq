"""Synchronize the public QQ command panel and remove legacy entries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from botpy.http import Route

from aiqq.interfaces.qq.commands import (
    GPT_IMAGE_COMMAND,
    MENU_COMMAND,
    NOVELAI_IMAGE_COMMAND,
    NOVELAI_PROMPT_COMMAND,
)


PANEL_SCOPE = "group"
PANEL_REMARK = "AiQQ 群聊指令面板"
LEGACY_CLEAR_MEMORY_COMMAND = "清除记忆"
MANAGED_COMMANDS = {
    MENU_COMMAND,
    NOVELAI_PROMPT_COMMAND,
    NOVELAI_IMAGE_COMMAND,
    GPT_IMAGE_COMMAND,
    LEGACY_CLEAR_MEMORY_COMMAND,
}


@dataclass(frozen=True)
class PanelSyncResult:
    action: str
    panel_id: str


def command_items() -> list[dict[str, Any]]:
    return [
        _item(MENU_COMMAND, "打开机器人快捷功能菜单"),
        _item(NOVELAI_PROMPT_COMMAND, "将画面描述转换为生图提示词"),
        _item(NOVELAI_IMAGE_COMMAND, "输入提示词生成图片"),
    ]


class PanelService:
    def __init__(self, http_client: Any) -> None:
        self._http = http_client

    async def sync(self) -> PanelSyncResult:
        records = await self._list_group_panels()
        global_records = [
            record
            for record in records
            if isinstance(record, dict) and record.get("target_type") == "all"
        ]
        managed = next(
            (
                record
                for record in global_records
                if _panel(record).get("remark") == PANEL_REMARK
            ),
            None,
        )
        if managed is not None:
            return await self._replace_managed_panel(managed)

        shared = next(
            (
                record
                for record in global_records
                if any(_is_managed(item) for item in _items(record))
            ),
            None,
        )
        if shared is not None:
            return await self._replace_commands_in_shared_panel(shared)

        response = await self._http.request(
            Route("POST", "/v2/panels"),
            json={
                "scope": PANEL_SCOPE,
                "target_type": "all",
                "panel": {"items": command_items(), "remark": PANEL_REMARK},
            },
        )
        panel_id = response.get("panel_id", "") if isinstance(response, dict) else ""
        return PanelSyncResult("created", str(panel_id))

    async def _list_group_panels(self) -> list[dict[str, Any]]:
        response = await self._http.request(
            Route("GET", "/v2/panels"),
            params={"scope": PANEL_SCOPE, "limit": 50},
        )
        records = response.get("records", []) if isinstance(response, dict) else []
        return records if isinstance(records, list) else []

    async def _replace_managed_panel(
        self, record: dict[str, Any]
    ) -> PanelSyncResult:
        panel_id = str(record.get("panel_id", ""))
        desired = {"items": command_items(), "remark": PANEL_REMARK}
        if _normalized_panel(_panel(record)) == desired:
            return PanelSyncResult("unchanged", panel_id)
        await self._update(panel_id, desired)
        return PanelSyncResult("updated", panel_id)

    async def _replace_commands_in_shared_panel(
        self, record: dict[str, Any]
    ) -> PanelSyncResult:
        panel_id = str(record.get("panel_id", ""))
        current = _panel(record)
        retained = [
            _normalize(item) for item in _items(record) if not _is_managed(item)
        ]
        desired = {
            "items": [*retained, *command_items()],
            "remark": str(current.get("remark", "")),
        }
        if _normalized_panel(current) == desired:
            return PanelSyncResult("unchanged", panel_id)
        await self._update(panel_id, desired)
        return PanelSyncResult("updated", panel_id)

    async def _update(self, panel_id: str, panel: dict[str, Any]) -> None:
        await self._http.request(
            Route("PUT", "/v2/panels/{panel_id}", panel_id=panel_id),
            json={"panel": panel},
        )


def _item(name: str, description: str) -> dict[str, Any]:
    return {
        "type": "command",
        "name": name,
        "desc": description,
        "only_admin": False,
    }


def _panel(record: dict[str, Any]) -> dict[str, Any]:
    panel = record.get("panel")
    return panel if isinstance(panel, dict) else {}


def _items(record: dict[str, Any]) -> list[dict[str, Any]]:
    items = _panel(record).get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _is_managed(item: object) -> bool:
    return (
        isinstance(item, dict)
        and item.get("type") == "command"
        and item.get("name") in MANAGED_COMMANDS
    )


def _normalize(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": item.get("type", ""),
        "name": item.get("name", ""),
        "desc": item.get("desc", ""),
        "only_admin": bool(item.get("only_admin", False)),
        **({"link": item["link"]} if item.get("link") else {}),
    }


def _normalized_panel(panel: dict[str, Any]) -> dict[str, Any]:
    raw_items = panel.get("items")
    items = raw_items if isinstance(raw_items, list) else []
    return {
        "items": [_normalize(item) for item in items if isinstance(item, dict)],
        "remark": str(panel.get("remark", "")),
    }
