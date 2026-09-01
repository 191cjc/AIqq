import unittest

from commands import (
    MEMORY_CLEAR_COMMANDS,
    extract_menu_suggestion,
    extract_novelai_prompt,
    extract_novelai_prompt_edit_request,
    extract_novelai_prompt_request,
)


class CommandTests(unittest.TestCase):
    def test_menu_commands_are_recognized(self):
        for command in ("菜单", "/菜单", "功能菜单", "/功能菜单"):
            self.assertEqual(extract_menu_suggestion(command), "")

    def test_menu_command_can_carry_an_image_suggestion(self):
        self.assertEqual(
            extract_menu_suggestion("/菜单 white cat, morning sunlight"),
            "white cat, morning sunlight",
        )

    def test_extracts_only_text_after_novelai_command(self):
        self.assertEqual(
            extract_novelai_prompt("NovelAI生图 1girl --seed 123"),
            "1girl --seed 123",
        )

    def test_empty_novelai_command_is_recognized(self):
        self.assertEqual(extract_novelai_prompt("NovelAI生图"), "")

    def test_panel_slash_command_is_recognized(self):
        self.assertEqual(
            extract_novelai_prompt("/NovelAI生图 silver hair, blue eyes"),
            "silver hair, blue eyes",
        )
        self.assertEqual(extract_novelai_prompt("/NovelAI生图"), "")

    def test_panel_slash_clear_memory_command_is_recognized(self):
        self.assertIn("/清除记忆", MEMORY_CLEAR_COMMANDS)

    def test_novelai_prompt_request_accepts_chinese_description(self):
        self.assertEqual(
            extract_novelai_prompt_request(
                "NovelAI提示词 一位站在樱花树下的白发少女"
            ),
            "一位站在樱花树下的白发少女",
        )
        self.assertEqual(
            extract_novelai_prompt_request("/NovelAI提示词 夜晚的未来城市"),
            "夜晚的未来城市",
        )
        self.assertEqual(extract_novelai_prompt_request("/NovelAI提示词"), "")

    def test_novelai_prompt_edit_request_carries_token_and_changes(self):
        self.assertEqual(
            extract_novelai_prompt_edit_request(
                "修改NovelAI提示词 session-token 方案2改成夜景"
            ),
            ("session-token", "方案2改成夜景"),
        )
        self.assertEqual(
            extract_novelai_prompt_edit_request(
                "/修改NovelAI提示词 session-token"
            ),
            ("session-token", ""),
        )

    def test_similar_text_is_not_treated_as_command(self):
        self.assertIsNone(extract_menu_suggestion("菜单测试"))
        self.assertIsNone(extract_novelai_prompt("NovelAI生图测试"))
        self.assertIsNone(extract_novelai_prompt("/NovelAI生图测试"))
        self.assertIsNone(extract_novelai_prompt("请NovelAI生图"))
        self.assertIsNone(extract_novelai_prompt_request("NovelAI提示词测试"))
        self.assertIsNone(extract_novelai_prompt_request("请NovelAI提示词"))
        self.assertIsNone(
            extract_novelai_prompt_edit_request("修改NovelAI提示词测试")
        )


if __name__ == "__main__":
    unittest.main()
