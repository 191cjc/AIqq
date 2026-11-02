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

    def test_nai_v5_short_command_preserves_scene_and_command_boundaries(self):
        for scene in ("2girls, church|girl, blue eyes,\n 1.1::white hair::", "白猫躺在窗边"):
            for prefix in ("Nai v5", "/Nai v5"):
                with self.subTest(scene=scene, prefix=prefix):
                    command = parse_command(f"  {prefix}\t{scene}  ")
                    self.assertEqual(command.name, "nai_v5_image")
                    self.assertEqual(command.argument, scene)
                    self.assertEqual(parse_command(f"{prefix} \n ").argument, "")
        for content in ("Nai v50 cat", "/Nai v5cat", "请介绍 Nai v5", "/Nai v5 作图cat", "/Nai v5 画风watercolor"):
            with self.subTest(content=content):
                self.assertIsNone(parse_command(content))

    def test_nai_v5_subcommands_take_precedence_over_short_image_command(self):
        for prefix in ("Nai v5", "/Nai v5"):
            for suffix, name in (("作图", "nai_v5_image"), ("画风", "nai_v5_style")):
                with self.subTest(prefix=prefix, suffix=suffix):
                    self.assertEqual(parse_command(f"{prefix} {suffix}").name, name)
                    command = parse_command(f"{prefix} {suffix} white cat")
                    self.assertEqual(command.name, name)
                    self.assertEqual(command.argument, "white cat")

    def test_style_command_tolerates_zero_width_prefix_and_label_case(self):
        for content in (
            "\u200b NovelAI画风 1.2::artist:mignon::",
            "\ufeff/NovelAI画风 1.2::artist:mignon::",
            "NovelAi画风   1.2::artist:mignon::",
            "/novelai画风 1.2::artist:mignon::",
        ):
            with self.subTest(content=content):
                command = parse_command(content)
                self.assertEqual(command.name, "novelai_style")
                self.assertEqual(command.argument, "1.2::artist:mignon::")
        self.assertEqual(parse_command("nai V5 画风 watercolor").name, "nai_v5_style")
        self.assertIsNone(parse_command("/nai v5 画风watercolor"))

    def test_feature_menu_has_no_gpt_or_memory_buttons(self):
        payload = str(feature_menu_keyboard())
        self.assertIn("NovelAI提示词", payload)
        self.assertIn("NovelAI生图", payload)
        self.assertIn("NovelAI画风", payload)
        self.assertIn("Nai v5 画风", payload)
        self.assertNotIn("NovelAI快速生图", payload)
        self.assertNotIn("GPT生图", payload)
        self.assertNotIn("清除记忆", payload)

    def test_quick_image_command_preserves_description_and_has_no_collisions(self):
        description = "一位白发少女\n站在樱花树下"
        for prefix in ("NovelAI快速生图", "/NovelAI快速生图"):
            command = parse_command(f"  {prefix}  {description}  ")
            self.assertEqual(command.name, "novelai_quick_image")
            self.assertEqual(command.argument, description)
            self.assertEqual(parse_command(f"{prefix} \n ").argument, "")
        self.assertIsNone(parse_command("NovelAI快速生图白猫"))
        self.assertEqual(parse_command("NovelAI生图 white cat").name, "novelai_image")
        self.assertEqual(parse_command("NovelAI提示词 白猫").name, "novelai_prompt")

    def test_style_command_preserves_weights_and_internal_newlines(self):
        for label, name in (("NovelAI画风", "novelai_style"), ("Nai v5 画风", "nai_v5_style")):
            for prefix in (label, f"/{label}"):
                command = parse_command(f" {prefix}  {{{{watercolor}}}},\nsoft lighting ")
                self.assertEqual(command.name, name)
                self.assertEqual(command.argument, "{{watercolor}},\nsoft lighting")
                self.assertEqual(parse_command(f"{prefix} \n ").argument, "")
            self.assertIsNone(parse_command(f"{label}watercolor"))

    def test_style_button_only_fills_input_without_clearing(self):
        buttons = [
            button
            for row in feature_menu_keyboard()["content"]["rows"]
            for button in row["buttons"]
        ]
        for name, label in (("novelai_style", "NovelAI画风"), ("nai_v5_style", "Nai v5 画风")):
            style = next(button for button in buttons if button["id"] == name)
            self.assertEqual(style["action"]["data"], label)
            self.assertFalse(style["action"]["enter"])
        self.assertLessEqual(len(feature_menu_keyboard()["content"]["rows"]), 5)

    def test_quick_menu_can_include_full_reply_link(self):
        payload = quick_menu_keyboard(full_reply_url="https://public.example/reply/id")
        self.assertEqual(len(payload["content"]["rows"]), 2)

    def test_quick_menu_can_include_generation_prompt_link(self):
        prompt_url = "https://public.example/reply/prompt"
        payload = quick_menu_keyboard(
            full_reply_url="https://public.example/reply/full",
            generation_prompt_url=prompt_url,
        )
        rows = payload["content"]["rows"]
        self.assertEqual(len(rows), 3)
        button = rows[-1]["buttons"][0]
        self.assertEqual(button["id"], "view_generation_prompt")
        self.assertEqual(button["render_data"]["label"], "查看本次提示词")
        self.assertEqual(button["action"]["type"], 0)
        self.assertEqual(button["action"]["data"], prompt_url)

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

    def test_image_link_uses_existing_url_action_with_full_output_and_prompt(self):
        image_url = "https://public.example/media/image.png"
        payload = quick_menu_keyboard(
            full_reply_url="https://public.example/reply/full",
            generation_prompt_url="https://public.example/reply/prompt?view=prompt",
            image_url=image_url,
        )
        rows = payload["content"]["rows"]
        self.assertEqual(len(rows), 4)
        button = rows[-1]["buttons"][0]
        self.assertEqual(button["id"], "view_image")
        self.assertEqual(button["render_data"]["label"], "查看图片")
        self.assertEqual(button["render_data"]["visited_label"], "查看图片")
        self.assertEqual(button["action"], {
            "type": 0, "permission": {"type": 2}, "data": image_url,
        })


if __name__ == "__main__":
    unittest.main()
