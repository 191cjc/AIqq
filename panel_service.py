from dataclasses import dataclass
from typing import Any

from botpy.http import Route

from commands import (
    CLEAR_MEMORY_COMMAND,
    MENU_COMMAND,
    NOVELAI_IMAGE_COMMAND,
    NOVELAI_PROMPT_COMMAND,
)


PANEL_SCOPE = "group"
PANEL_REMARK = "AiQQ 群聊指令面板"
CLEAR_MEMORY_DESCRIPTION = "清除你的对话上下文"
NOVELAI_IMAGE_DESCRIPTION = "输入提示词生成图片"
NOVELAI_PROMPT_DESCRIPTION = "将画面描述转换为生图提示词"
MENU_DESCRIPTION = "打开机器人快捷功能菜单"


def _command_items() -> list[dict[str, Any]]:
    return [
        {
            "type": "command",
            "name": MENU_COMMAND,
            "desc": MENU_DESCRIPTION,
            "only_admin": False,
        },
        {
            "type": "command",
            "name": CLEAR_MEMORY_COMMAND,
            "desc": CLEAR_MEMORY_DESCRIPTION,
            "only_admin": False,
        },
        {
            "type": "command",
            "name": NOVELAI_PROMPT_COMMAND,
            "desc": NOVELAI_PROMPT_DESCRIPTION,
            "only_admin": False,
        },
        {
            "type": "command",
            "name": NOVELAI_IMAGE_COMMAND,
            "desc": NOVELAI_IMAGE_DESCRIPTION,
            "only_admin": False,
        },
    ]


def _panel() -> dict[str, Any]:
    return {"items": _command_items(), "remark": PANEL_REMARK}


def _normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "type": item.get("type", ""),
        "name": item.get("name", ""),
        "desc": item.get("desc", ""),
        "only_admin": bool(item.get("only_admin", False)),
    }
    if item.get("link"):
        normalized["link"] = item["link"]
    return normalized


def _is_managed_command_item(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and item.get("type") == "command"
        and item.get("name")
        in {
            MENU_COMMAND,
            CLEAR_MEMORY_COMMAND,
            NOVELAI_IMAGE_COMMAND,
            NOVELAI_PROMPT_COMMAND,
        }
    )


@dataclass(frozen=True)
class PanelSyncResult:
    action: str
    panel_id: str


class PanelService:
    """Synchronize AiQQ's group command panel through the logged-in SDK."""

    def __init__(self, http_client):
        self._http = http_client

    async def sync_clear_memory_panel(self) -> PanelSyncResult:
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
                if self._record_panel(record).get("remark") == PANEL_REMARK
            ),
            None,
        )
        if managed is not None:
            return await self._sync_managed_panel(managed)

        existing = next(
            (
                record
                for record in global_records
                if any(
                    _is_managed_command_item(item)
                    for item in self._record_panel(record).get("items", [])
                )
            ),
            None,
        )
        if existing is not None:
            return await self._sync_existing_command(existing)

        response = await self._http.request(
            Route("POST", "/v2/panels"),
            json={
                "scope": PANEL_SCOPE,
                "target_type": "all",
                "panel": _panel(),
            },
        )
        panel_id = response.get("panel_id", "") if isinstance(response, dict) else ""
        return PanelSyncResult("created", panel_id)

    async def _list_group_panels(self) -> list[dict[str, Any]]:
        response = await self._http.request(
            Route("GET", "/v2/panels"),
            params={"scope": PANEL_SCOPE, "limit": 50},
        )
        if not isinstance(response, dict):
            return []
        records = response.get("records", [])
        return records if isinstance(records, list) else []

    async def _sync_managed_panel(self, record: dict[str, Any]) -> PanelSyncResult:
        panel_id = str(record.get("panel_id", ""))
        current = self._record_panel(record)
        current_items = current.get("items", [])
        items_match = isinstance(current_items, list) and [
            _normalize_item(item) for item in current_items if isinstance(item, dict)
        ] == _command_items()
        if items_match:
            return PanelSyncResult("unchanged", panel_id)

        await self._update_panel(panel_id, _panel())
        return PanelSyncResult("updated", panel_id)

    async def _sync_existing_command(
        self, record: dict[str, Any]
    ) -> PanelSyncResult:
        panel_id = str(record.get("panel_id", ""))
        panel = self._record_panel(record)
        raw_items = panel.get("items", [])
        items = [
            _normalize_item(item)
            for item in raw_items
            if isinstance(item, dict)
        ]

        changed = False
        desired_by_name = {item["name"]: item for item in _command_items()}
        existing_names = set()
        for index, item in enumerate(items):
            name = item.get("name")
            if name in desired_by_name:
                existing_names.add(name)
                if item != desired_by_name[name]:
                    items[index] = desired_by_name[name]
                    changed = True

        for name, desired in desired_by_name.items():
            if name not in existing_names:
                items.append(desired)
                changed = True

        if not changed:
            return PanelSyncResult("unchanged", panel_id)

        await self._update_panel(
            panel_id,
            {"items": items, "remark": str(panel.get("remark", ""))},
        )
        return PanelSyncResult("updated", panel_id)

    async def _update_panel(self, panel_id: str, panel: dict[str, Any]) -> None:
        await self._http.request(
            Route("PUT", "/v2/panels/{panel_id}", panel_id=panel_id),
            json={"panel": panel},
        )

    @staticmethod
    def _record_panel(record: dict[str, Any]) -> dict[str, Any]:
        panel = record.get("panel", {})
        return panel if isinstance(panel, dict) else {}
