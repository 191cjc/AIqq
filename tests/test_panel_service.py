import unittest

from panel_service import (
    CLEAR_MEMORY_COMMAND,
    CLEAR_MEMORY_DESCRIPTION,
    GPT_IMAGE_COMMAND,
    GPT_IMAGE_DESCRIPTION,
    MENU_COMMAND,
    MENU_DESCRIPTION,
    NOVELAI_IMAGE_COMMAND,
    NOVELAI_IMAGE_DESCRIPTION,
    NOVELAI_PROMPT_COMMAND,
    NOVELAI_PROMPT_DESCRIPTION,
    PANEL_REMARK,
    PanelService,
)


class FakeHttp:
    def __init__(self, list_response, mutation_response=None):
        self.list_response = list_response
        self.mutation_response = mutation_response or {}
        self.calls = []

    async def request(self, route, **kwargs):
        self.calls.append(
            {
                "method": route.method,
                "path": route.path,
                "parameters": route.parameters,
                **kwargs,
            }
        )
        if route.method == "GET":
            return self.list_response
        return self.mutation_response


def command_item(desc=CLEAR_MEMORY_DESCRIPTION, only_admin=False):
    return {
        "type": "command",
        "name": CLEAR_MEMORY_COMMAND,
        "desc": desc,
        "only_admin": only_admin,
    }


def image_command_item():
    return {
        "type": "command",
        "name": NOVELAI_IMAGE_COMMAND,
        "desc": NOVELAI_IMAGE_DESCRIPTION,
        "only_admin": False,
    }


def prompt_command_item():
    return {
        "type": "command",
        "name": NOVELAI_PROMPT_COMMAND,
        "desc": NOVELAI_PROMPT_DESCRIPTION,
        "only_admin": False,
    }


def gpt_image_command_item():
    return {
        "type": "command",
        "name": GPT_IMAGE_COMMAND,
        "desc": GPT_IMAGE_DESCRIPTION,
        "only_admin": False,
    }


def menu_command_item():
    return {
        "type": "command",
        "name": MENU_COMMAND,
        "desc": MENU_DESCRIPTION,
        "only_admin": False,
    }


class PanelServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_creates_global_group_panel_when_missing(self):
        http = FakeHttp({"records": []}, {"panel_id": "panel-new"})

        result = await PanelService(http).sync_clear_memory_panel()

        self.assertEqual(result.action, "created")
        self.assertEqual(result.panel_id, "panel-new")
        self.assertEqual(http.calls[0]["params"], {"scope": "group", "limit": 50})
        self.assertEqual(
            http.calls[1],
            {
                "method": "POST",
                "path": "/v2/panels",
                "parameters": {},
                "json": {
                    "scope": "group",
                    "target_type": "all",
                    "panel": {
                        "items": [
                            menu_command_item(),
                            command_item(),
                            prompt_command_item(),
                            image_command_item(),
                            gpt_image_command_item(),
                        ],
                        "remark": PANEL_REMARK,
                    },
                },
            },
        )

    async def test_does_not_update_matching_managed_panel(self):
        http = FakeHttp(
            {
                "records": [
                    {
                        "panel_id": "panel-existing",
                        "target_type": "all",
                        "panel": {
                            "items": [
                                menu_command_item(),
                                command_item(),
                                prompt_command_item(),
                                image_command_item(),
                                gpt_image_command_item(),
                            ],
                            "remark": PANEL_REMARK,
                        },
                    }
                ]
            }
        )

        result = await PanelService(http).sync_clear_memory_panel()

        self.assertEqual(result.action, "unchanged")
        self.assertEqual(len(http.calls), 1)

    async def test_updates_outdated_managed_panel(self):
        http = FakeHttp(
            {
                "records": [
                    {
                        "panel_id": "panel-old",
                        "target_type": "all",
                        "panel": {
                            "items": [command_item(desc="旧说明")],
                            "remark": PANEL_REMARK,
                        },
                    }
                ]
            }
        )

        result = await PanelService(http).sync_clear_memory_panel()

        self.assertEqual(result.action, "updated")
        self.assertEqual(http.calls[1]["method"], "PUT")
        self.assertEqual(http.calls[1]["path"], "/v2/panels/{panel_id}")
        self.assertEqual(http.calls[1]["parameters"], {"panel_id": "panel-old"})
        self.assertEqual(
            http.calls[1]["json"]["panel"]["items"],
            [
                menu_command_item(),
                command_item(),
                prompt_command_item(),
                image_command_item(),
                gpt_image_command_item(),
            ],
        )

    async def test_reuses_command_in_another_global_panel(self):
        weather = {
            "type": "command",
            "name": "查询天气",
            "desc": "查询当前天气",
            "only_admin": False,
        }
        http = FakeHttp(
            {
                "records": [
                    {
                        "panel_id": "panel-shared",
                        "target_type": "all",
                        "panel": {
                            "items": [weather, command_item(desc="旧说明", only_admin=True)],
                            "remark": "已有面板",
                        },
                    }
                ]
            }
        )

        result = await PanelService(http).sync_clear_memory_panel()

        self.assertEqual(result.action, "updated")
        self.assertEqual(
            http.calls[1]["json"]["panel"],
            {
                "items": [
                    weather,
                    command_item(),
                    menu_command_item(),
                    prompt_command_item(),
                    image_command_item(),
                    gpt_image_command_item(),
                ],
                "remark": "已有面板",
            },
        )

    async def test_specific_group_panel_does_not_replace_global_panel(self):
        http = FakeHttp(
            {
                "records": [
                    {
                        "panel_id": "specific-panel",
                        "target_type": "specific",
                        "panel": {"items": [command_item()], "remark": PANEL_REMARK},
                    }
                ]
            },
            {"panel_id": "global-panel"},
        )

        result = await PanelService(http).sync_clear_memory_panel()

        self.assertEqual(result.action, "created")
        self.assertEqual(result.panel_id, "global-panel")
        self.assertEqual(http.calls[1]["json"]["target_type"], "all")


if __name__ == "__main__":
    unittest.main()
