import unittest

from aiqq.config import AppConfig
from aiqq.exceptions import ConfigurationError
from aiqq.interfaces.qq.commands import parse_command
from aiqq.interfaces.qq.ui import feature_menu_keyboard
from aiqq.logic.gpt_usage import (
    NOT_A_USER_TEXT,
    GPTUsageAccount,
    GPTUsageUnavailable,
    GPTUsageWorkflow,
)


PAYLOAD = {
    "balance": 223.03794941,
    "remaining": 223.03794941,
    "unit": "USD",
    "model_stats": [
        {"model": "gpt-5.6-sol", "requests": 1, "total_tokens": 16761,
         "cost": 0.027281, "actual_cost": 0.00682025},
    ],
    "usage": {
        "today": {"requests": 2, "total_tokens": 32233, "cost": 0.0277,
                  "actual_cost": 0.00691579},
        "total": {"requests": 2, "total_tokens": 32233, "cost": 0.0277,
                  "actual_cost": 0.00691579},
    },
}


class FakeFetcher:
    def __init__(self, failing=()):
        self.calls = []
        self.failing = set(failing)

    async def fetch(self, api_key):
        self.calls.append(api_key)
        if api_key in self.failing:
            raise GPTUsageUnavailable("HTTP 500")
        return PAYLOAD


ACCOUNTS = (
    GPTUsageAccount("farmer", "农夫3", "sk-farmer"),
    GPTUsageAccount("grass", "墙角的青草", "sk-grass"),
)


class GPTUsageWorkflowTest(unittest.IsolatedAsyncioTestCase):
    def workflow(self, fetcher):
        return GPTUsageWorkflow(
            fetcher=fetcher, accounts=ACCOUNTS, admin_member_ids={"admin"}
        )

    async def test_mapped_user_sees_only_own_usage(self):
        fetcher = FakeFetcher()
        result = await self.workflow(fetcher).run(member_openid="farmer")
        self.assertEqual(result.status, "ok")
        self.assertEqual(fetcher.calls, ["sk-farmer"])
        self.assertEqual(
            result.full_text,
            "**农夫3**\n累计已消耗：¥0.0069\n今日已消耗：¥0.0069",
        )
        self.assertEqual(result.summary, "农夫3：累计已消耗¥0.0069，今日¥0.0069")
        self.assertNotIn("sk-", result.full_text)
        self.assertLessEqual(len(result.summary), 50)

    async def test_unmapped_user_is_rejected_without_request(self):
        fetcher = FakeFetcher()
        result = await self.workflow(fetcher).run(member_openid="stranger")
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.full_text, NOT_A_USER_TEXT)
        self.assertEqual(fetcher.calls, [])
        result = await self.workflow(fetcher).run(member_openid="")
        self.assertEqual(result.status, "rejected")

    async def test_admin_sees_every_mapping_and_partial_failures(self):
        fetcher = FakeFetcher(failing={"sk-grass"})
        result = await self.workflow(fetcher).run(member_openid="admin")
        self.assertEqual(result.status, "ok")
        self.assertEqual(sorted(fetcher.calls), ["sk-farmer", "sk-grass"])
        self.assertIn("农夫3", result.full_text)
        self.assertIn("墙角的青草", result.full_text)
        self.assertIn("查询失败", result.full_text)
        self.assertEqual(result.summary, "累计已消耗：农夫3¥0.0069；墙角的青草查询失败")

    async def test_fetch_failure_is_unavailable(self):
        result = await self.workflow(FakeFetcher(failing={"sk-farmer"})).run(
            member_openid="farmer"
        )
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.error_code, "gpt_usage_unavailable")


class GPTUsageCommandTest(unittest.TestCase):
    def test_command_and_menu_button(self):
        self.assertEqual(parse_command("/GPT用量").name, "gpt_usage")
        self.assertEqual(parse_command("gpt用量").name, "gpt_usage")
        self.assertEqual(parse_command("GPT生图 猫").name, "gpt_image")
        buttons = [
            button
            for row in feature_menu_keyboard()["content"]["rows"]
            for button in row["buttons"]
        ]
        usage = next(button for button in buttons if button["id"] == "gpt_usage")
        self.assertEqual(usage["action"]["data"], "GPT用量")
        self.assertTrue(usage["action"]["enter"])


class GPTUsageConfigTest(unittest.TestCase):
    BASE = {
        "QQ_BOT_APPID": "app",
        "QQ_BOT_SECRET": "secret",
        "OPENAI_API_KEY": "key",
    }

    def test_parses_accounts_and_admins(self):
        config = AppConfig.from_env({
            **self.BASE,
            "AIQQ_GPT_USAGE_ACCOUNTS": "A|农夫3|sk-a; B|墙角的青草|sk-b",
            "AIQQ_GPT_USAGE_ADMINS": "ADMIN",
        })
        usage = config.gpt_usage
        self.assertEqual(usage.base_url, "https://api.airoo.cc/v1")
        self.assertEqual(
            [(a.member_openid, a.label, a.api_key) for a in usage.accounts],
            [("A", "农夫3", "sk-a"), ("B", "墙角的青草", "sk-b")],
        )
        self.assertEqual(usage.admin_member_ids, frozenset({"ADMIN"}))

    def test_rejects_malformed_accounts(self):
        for raw in ("A|sk-a", "A|x|sk-a;A|y|sk-b", "A||sk-a"):
            with self.assertRaises(ConfigurationError):
                AppConfig.from_env({**self.BASE, "AIQQ_GPT_USAGE_ACCOUNTS": raw})


if __name__ == "__main__":
    unittest.main()
