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


if __name__ == "__main__":
    unittest.main()
