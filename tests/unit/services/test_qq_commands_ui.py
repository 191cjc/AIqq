import unittest

from aiqq.interfaces.qq.commands import parse_command
from aiqq.interfaces.qq.ui import (
    feature_menu_keyboard,
    novelai_prompt_options_keyboard,
    quick_menu_keyboard,
)


class QQCommandsAndUITests(unittest.TestCase):
    def test_gpt_image_remains_a_text_command(self):
        command = parse_command("/GPT生图 画一只白猫")
        self.assertEqual(command.name, "gpt_image")
        self.assertEqual(command.argument, "画一只白猫")

    def test_clear_memory_is_not_a_command(self):
        self.assertIsNone(parse_command("清除记忆"))
        self.assertIsNone(parse_command("/新对话"))

    def test_feature_menu_has_no_gpt_or_memory_buttons(self):
        payload = str(feature_menu_keyboard())
        self.assertIn("NovelAI提示词", payload)
        self.assertIn("NovelAI生图", payload)
        self.assertNotIn("GPT生图", payload)
        self.assertNotIn("清除记忆", payload)

    def test_quick_menu_can_include_full_reply_link(self):
        payload = quick_menu_keyboard(full_reply_url="https://public.example/reply/id")
        self.assertEqual(len(payload["content"]["rows"]), 2)

    def test_novelai_options_include_generation_revision_and_full_reply_actions(self):
        prompts = ("first, safe, sfw", "second, safe, sfw", "third, safe, sfw")
        payload = novelai_prompt_options_keyboard(
            prompts,
            "token",
            full_reply_url="https://public.example/reply/id",
        )
        rows = payload["content"]["rows"]
        self.assertEqual(len(rows), 5)
        self.assertEqual(
            rows[0]["buttons"][0]["action"]["data"],
            "NovelAI生图 first, safe, sfw",
        )
        self.assertEqual(
            rows[3]["buttons"][0]["action"]["data"],
            "修改NovelAI提示词 token ",
        )
        self.assertEqual(
            rows[4]["buttons"][0]["action"]["data"],
            "https://public.example/reply/id",
        )


if __name__ == "__main__":
    unittest.main()
