import unittest

from aiqq.config import AppConfig
from aiqq.exceptions import ConfigurationError


BASE_ENV = {
    "QQ_BOT_APPID": "app-id",
    "QQ_BOT_SECRET": "secret",
    "OPENAI_API_KEY": "api-key",
}


class ConfigTests(unittest.TestCase):
    def test_image_audit_whitelist_defaults_empty_and_is_independent_of_quota(self):
        defaults = AppConfig.from_env(BASE_ENV)
        self.assertEqual(defaults.images.image_audit_whitelist, frozenset())
        quota_only = AppConfig.from_env({**BASE_ENV, "AIQQ_NAI_V5_WHITELIST": "quota-member"})
        self.assertEqual(quota_only.images.image_audit_whitelist, frozenset())
        config = AppConfig.from_env({
            **BASE_ENV, "AIQQ_NAI_V5_WHITELIST": "quota-member",
            "AIQQ_IMAGE_AUDIT_WHITELIST": " first-account, second-account,first-account ",
        })
        self.assertEqual(config.images.image_audit_whitelist,
                         frozenset({"first-account", "second-account"}))
        self.assertEqual(config.images.nai_v5_whitelist, frozenset({"quota-member"}))

    def test_image_audit_whitelist_rejects_malformed_member_lists(self):
        for value in ("first,,second", "first,", "first,second account", "x" * 257):
            with self.subTest(value=value), self.assertRaises(ConfigurationError):
                AppConfig.from_env({**BASE_ENV, "AIQQ_IMAGE_AUDIT_WHITELIST": value})

    def test_codex_sdk_and_high_reasoning_are_the_defaults(self):
        config = AppConfig.from_env(BASE_ENV)

        self.assertEqual(config.ai.backend, "codex_sdk")
        self.assertEqual(config.ai.reasoning_effort, "high")
        self.assertEqual(config.ai.utility_reasoning_effort, "high")
        self.assertTrue(config.ai.gpt_image_skill_enabled)

    def test_image_driver_is_independent_of_chat_and_tool_models(self):
        config = AppConfig.from_env({**BASE_ENV, "OPENAI_MODEL": "chat-model"})

        self.assertEqual(config.images.driver_model, "gpt-6-astra")
        self.assertEqual(config.images.model, "gpt-image-2.5-flare")
        overridden = AppConfig.from_env({
            **BASE_ENV,
            "OPENAI_IMAGE_DRIVER_MODEL": "custom-driver",
            "OPENAI_IMAGE_MODEL": "custom-image-tool",
        })
        self.assertEqual(overridden.images.driver_model, "custom-driver")
        self.assertEqual(overridden.images.model, "custom-image-tool")

    def test_blank_image_driver_uses_verified_default(self):
        config = AppConfig.from_env({**BASE_ENV, "OPENAI_IMAGE_DRIVER_MODEL": " "})

        self.assertEqual(config.images.driver_model, "gpt-6-astra")

    def test_blank_image_credentials_and_model_use_documented_defaults(self):
        config = AppConfig.from_env({
            **BASE_ENV,
            "OPENAI_BASE_URL": "https://chat.example/v1",
            "OPENAI_IMAGE_API_KEY": " ",
            "OPENAI_IMAGE_BASE_URL": "",
            "OPENAI_IMAGE_MODEL": " ",
        })

        self.assertEqual(config.images.api_key, config.ai.api_key)
        self.assertEqual(config.images.base_url, "https://chat.example/v1")
        self.assertEqual(config.images.model, "gpt-image-2.5-flare")

    def test_removed_app_server_and_xhigh_values_are_rejected(self):
        with self.assertRaises(ConfigurationError):
            AppConfig.from_env({**BASE_ENV, "AIQQ_AI_BACKEND": "codex_app_server"})
        with self.assertRaises(ConfigurationError):
            AppConfig.from_env(
                {**BASE_ENV, "AIQQ_CODEX_REASONING_EFFORT": "xhigh"}
            )


if __name__ == "__main__":
    unittest.main()
