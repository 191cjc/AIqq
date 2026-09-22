import unittest

from aiqq.services.qq.panel import PANEL_REMARK, PanelService, command_items


class FakeHTTP:
    def __init__(self, records):
        self.records = records
        self.calls = []

    async def request(self, route, **kwargs):
        self.calls.append((route.method, route.path, kwargs))
        if route.method == "GET":
            return {"records": self.records}
        return {"panel_id": "new-panel"}


class QQPanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_managed_panel_removes_legacy_commands(self):
        http = FakeHTTP(
            [
                {
                    "panel_id": "managed",
                    "target_type": "all",
                    "panel": {
                        "remark": PANEL_REMARK,
                        "items": [
                            {
                                "type": "command",
                                "name": "清除记忆",
                                "desc": "old",
                                "only_admin": False,
                            },
                            {
                                "type": "command",
                                "name": "GPT生图",
                                "desc": "old",
                                "only_admin": False,
                            },
                        ],
                    },
                }
            ]
        )

        result = await PanelService(http).sync()

        self.assertEqual(result.action, "updated")
        updated = http.calls[1][2]["json"]["panel"]["items"]
        self.assertEqual(updated, command_items())
        self.assertNotIn("清除记忆", str(updated))
        self.assertNotIn("GPT生图", str(updated))
        self.assertEqual(
            [item["name"] for item in updated].count("NovelAI画风"), 1
        )
        self.assertEqual(
            [item["name"] for item in updated].count("NovelAI快速生图"), 1
        )
        style = next(item for item in updated if item["name"] == "NovelAI画风")
        self.assertFalse(style["only_admin"])
        self.assertIn("清除", style["desc"])
        quick = next(item for item in updated if item["name"] == "NovelAI快速生图")
        self.assertFalse(quick["only_admin"])
        self.assertIn("直接", quick["desc"])

    async def test_matching_panel_is_not_rewritten(self):
        http = FakeHTTP(
            [
                {
                    "panel_id": "managed",
                    "target_type": "all",
                    "panel": {"remark": PANEL_REMARK, "items": command_items()},
                }
            ]
        )
        result = await PanelService(http).sync()
        self.assertEqual(result.action, "unchanged")
        self.assertEqual(len(http.calls), 1)

    async def test_shared_panel_keeps_unrelated_entries_and_replaces_managed_once(self):
        retained = [
            {
                "type": "command", "name": "群管理", "desc": "已有管理功能",
                "only_admin": True,
            },
            {
                "type": "link", "name": "群主页", "desc": "已有链接",
                "only_admin": False, "link": "https://example.com/group",
            },
        ]
        record = {
            "panel_id": "shared",
            "target_type": "all",
            "panel": {
                "remark": "shared with another feature",
                "items": [
                    *retained,
                    {"type": "command", "name": "NovelAI画风", "desc": "old"},
                    {"type": "command", "name": "NovelAI快速生图", "desc": "old"},
                    {"type": "command", "name": "NovelAI快速生图", "desc": "duplicate"},
                    {"type": "command", "name": "GPT生图", "desc": "legacy"},
                ],
            },
        }
        http = FakeHTTP([record])
        service = PanelService(http)

        result = await service.sync()

        self.assertEqual(result.action, "updated")
        updated = http.calls[1][2]["json"]["panel"]
        self.assertEqual(updated["remark"], record["panel"]["remark"])
        self.assertEqual(updated["items"], [*retained, *command_items()])
        self.assertEqual(
            [item["name"] for item in updated["items"]].count("NovelAI画风"), 1
        )
        self.assertEqual(
            [item["name"] for item in updated["items"]].count("NovelAI快速生图"), 1
        )
        record["panel"] = updated
        self.assertEqual((await service.sync()).action, "unchanged")
        self.assertEqual([call[0] for call in http.calls], ["GET", "PUT", "GET"])


if __name__ == "__main__":
    unittest.main()
