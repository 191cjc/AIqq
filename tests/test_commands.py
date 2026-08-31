import unittest

from commands import MEMORY_CLEAR_COMMANDS, extract_novelai_prompt


class CommandTests(unittest.TestCase):
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

    def test_similar_text_is_not_treated_as_command(self):
        self.assertIsNone(extract_novelai_prompt("NovelAI生图测试"))
        self.assertIsNone(extract_novelai_prompt("/NovelAI生图测试"))
        self.assertIsNone(extract_novelai_prompt("请NovelAI生图"))


if __name__ == "__main__":
    unittest.main()
