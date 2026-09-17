import unittest

from message_ui import (
    feature_menu_keyboard,
    markdown_reply_chunks,
    novelai_prompt_options_keyboard,
    quick_menu_keyboard,
    reply_feature_menu,
    reply_research_stage,
    reply_with_novelai_prompt_options,
    reply_with_quick_menu,
    split_message_content,
)


class FakeMessage:
    def __init__(self):
        self.reply_calls = []

    async def reply(self, **kwargs):
        self.reply_calls.append(kwargs)
        return {"id": "reply-id"}


class MessageUITests(unittest.IsolatedAsyncioTestCase):
    def test_long_content_splits_on_natural_boundaries(self):
        self.assertEqual(
            split_message_content("A" * 15 + "\n\n" + "B" * 15, 18),
            ("A" * 15, "B" * 15),
        )

    def test_splitter_fits_content_into_available_reply_slots(self):
        parts = split_message_content("A" * 100, 20, max_parts=3)

        self.assertEqual(len(parts), 3)
        self.assertEqual("".join(parts), "A" * 100)

    def test_short_reply_stays_as_normal_markdown(self):
        self.assertEqual(markdown_reply_chunks("A" * 50), ("A" * 50,))

    def test_reply_over_50_characters_stays_as_normal_markdown(self):
        self.assertEqual(markdown_reply_chunks("A" * 51), ("A" * 51,))

    def test_existing_code_fences_are_preserved(self):
        content = "说明文字" + "A" * 50 + "\n\n```text\ncode()\n```"

        (formatted,) = markdown_reply_chunks(content)

        self.assertEqual(formatted.count("```"), 2)
        self.assertIn("code()", formatted)
        self.assertIn("```text\ncode()", formatted)

    def test_long_reply_splits_without_code_block_wrapper(self):
        parts = markdown_reply_chunks("A" * 80, 60)

        self.assertEqual(len(parts), 2)
        self.assertTrue(all(len(part) <= 60 for part in parts))
        self.assertEqual("".join(parts), "A" * 80)
        self.assertTrue(all("```text" not in part for part in parts))

    def test_quick_menu_has_one_non_blue_button_and_executes_command(self):
        rows = quick_menu_keyboard()["content"]["rows"]

        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["buttons"]), 1)
        button = rows[0]["buttons"][0]
        self.assertEqual(button["render_data"]["label"], "功能菜单")
        self.assertEqual(button["render_data"]["style"], 0)
        self.assertEqual(button["action"]["type"], 2)
        self.assertEqual(button["action"]["permission"]["type"], 2)
        self.assertEqual(button["action"]["data"], "/菜单")
        self.assertTrue(button["action"]["enter"])

    def test_quick_menu_adds_full_reply_link_on_separate_row(self):
        full_reply_url = "https://example.com/aiqq/reply/random-token.txt"

        rows = quick_menu_keyboard(full_reply_url=full_reply_url)["content"][
            "rows"
        ]

        self.assertEqual(len(rows), 2)
        self.assertTrue(all(len(row["buttons"]) == 1 for row in rows))
        button = rows[1]["buttons"][0]
        self.assertEqual(button["render_data"]["label"], "查看完整输出")
        self.assertEqual(button["action"]["type"], 0)
        self.assertEqual(button["action"]["permission"]["type"], 2)
        self.assertEqual(button["action"]["data"], full_reply_url)

    def test_feature_menu_restores_all_command_buttons(self):
        rows = feature_menu_keyboard()["content"]["rows"]

        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["buttons"][0]["render_data"]["label"], "清除记忆")
        prompt_button = rows[1]["buttons"][0]
        self.assertEqual(prompt_button["render_data"]["label"], "NovelAI提示词")
        self.assertEqual(prompt_button["action"]["data"], "NovelAI提示词")
        self.assertFalse(prompt_button["action"]["enter"])
        image_button = rows[2]["buttons"][0]
        self.assertEqual(image_button["render_data"]["label"], "NovelAI生图")
        self.assertEqual(image_button["action"]["type"], 2)
        self.assertEqual(image_button["action"]["permission"]["type"], 2)
        self.assertEqual(image_button["action"]["data"], "NovelAI生图")
        self.assertFalse(image_button["action"]["enter"])
        gpt_image_button = rows[3]["buttons"][0]
        self.assertEqual(gpt_image_button["render_data"]["label"], "GPT生图")
        self.assertEqual(gpt_image_button["action"]["data"], "GPT生图")
        self.assertFalse(gpt_image_button["action"]["enter"])

    def test_suggestion_button_fills_complete_image_command(self):
        rows = feature_menu_keyboard(
            novelai_suggestion="white cat, morning sunlight"
        )["content"]["rows"]

        self.assertEqual(len(rows), 5)
        button = rows[4]["buttons"][0]
        self.assertEqual(button["render_data"]["label"], "🟢 使用建议")
        self.assertEqual(button["render_data"]["style"], 0)
        self.assertNotEqual(
            button["render_data"]["style"],
            rows[2]["buttons"][0]["render_data"]["style"],
        )
        self.assertEqual(button["action"]["type"], 2)
        self.assertEqual(button["action"]["permission"]["type"], 2)
        self.assertEqual(
            button["action"]["data"],
            "NovelAI生图 white cat, morning sunlight",
        )
        self.assertFalse(button["action"]["enter"])

    def test_generated_prompt_buttons_fill_options_and_edit_command(self):
        prompts = (
            "white-haired girl, cherry blossoms, safe, sfw",
            "white-haired girl, night scene, safe, sfw",
            "white-haired girl, portrait, safe, sfw",
        )
        rows = novelai_prompt_options_keyboard(prompts, "session-token")[
            "content"
        ]["rows"]

        self.assertEqual(len(rows), 4)
        for index, row in enumerate(rows[:3], 1):
            self.assertEqual(len(row["buttons"]), 1)
            button = row["buttons"][0]
            self.assertEqual(button["render_data"]["label"], f"🟢 使用方案{index}")
            self.assertEqual(button["render_data"]["style"], 0)
            self.assertEqual(button["action"]["type"], 2)
            self.assertEqual(button["action"]["permission"]["type"], 2)
            self.assertEqual(
                button["action"]["data"],
                f"NovelAI生图 {prompts[index - 1]}",
            )
            self.assertFalse(button["action"]["enter"])
        edit_button = rows[3]["buttons"][0]
        self.assertEqual(edit_button["render_data"]["label"], "修改提示词")
        self.assertEqual(
            edit_button["action"]["data"],
            "修改NovelAI提示词 session-token ",
        )
        self.assertFalse(edit_button["action"]["enter"])

    def test_generated_prompt_buttons_add_full_reply_link_as_fifth_row(self):
        prompts = (
            "white cat, safe, sfw",
            "white cat, daylight, safe, sfw",
            "white cat, portrait, safe, sfw",
        )
        full_reply_url = "https://example.com/aiqq/reply/random-token.txt"

        rows = novelai_prompt_options_keyboard(
            prompts,
            "session-token",
            full_reply_url=full_reply_url,
        )["content"]["rows"]

        self.assertEqual(len(rows), 5)
        copy_button = rows[4]["buttons"][0]
        self.assertEqual(copy_button["render_data"]["label"], "查看完整输出")
        self.assertEqual(copy_button["action"]["data"], full_reply_url)

    def test_feature_menu_can_add_full_reply_link(self):
        full_reply_url = "https://example.com/aiqq/reply/random-token.txt"

        rows = feature_menu_keyboard(full_reply_url=full_reply_url)["content"][
            "rows"
        ]

        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[4]["buttons"][0]["action"]["data"], full_reply_url)

    def test_quick_menu_adds_direct_novelai_suggestion_button(self):
        rows = quick_menu_keyboard(
            novelai_suggestion="white cat, morning sunlight"
        )["content"]["rows"]

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["buttons"][0]["action"]["data"], "/菜单")
        button = rows[1]["buttons"][0]
        self.assertEqual(button["render_data"]["label"], "🟢 使用建议")
        self.assertEqual(button["render_data"]["style"], 0)
        self.assertEqual(
            button["action"]["data"],
            "NovelAI生图 white cat, morning sunlight",
        )
        self.assertFalse(button["action"]["enter"])

    def test_quick_menu_adds_direct_gpt_suggestion_button(self):
        rows = quick_menu_keyboard(
            gpt_suggestion="white cat, morning sunlight"
        )["content"]["rows"]

        self.assertEqual(len(rows), 2)
        button = rows[1]["buttons"][0]
        self.assertEqual(button["render_data"]["label"], "🟢 使用建议")
        self.assertEqual(
            button["action"]["data"],
            "GPT生图 white cat, morning sunlight",
        )
        self.assertFalse(button["action"]["enter"])

    def test_quick_menu_carries_original_prompt_to_prompt_command(self):
        rows = quick_menu_keyboard(
            novelai_prompt_request="一只坐在窗边的白猫"
        )["content"]["rows"]

        button = rows[0]["buttons"][0]
        self.assertEqual(
            button["action"]["data"],
            "/菜单 NovelAI提示词 一只坐在窗边的白猫",
        )
        self.assertTrue(button["action"]["enter"])

    def test_prompt_request_button_opens_three_option_command(self):
        rows = feature_menu_keyboard(
            novelai_prompt_request="一只坐在窗边的白猫"
        )["content"]["rows"]

        self.assertEqual(len(rows), 5)
        button = rows[4]["buttons"][0]
        self.assertEqual(button["render_data"]["label"], "🟢 生成提示词方案")
        self.assertEqual(button["render_data"]["style"], 0)
        self.assertEqual(
            button["action"]["data"],
            "NovelAI提示词 一只坐在窗边的白猫",
        )
        self.assertFalse(button["action"]["enter"])

    async def test_reply_uses_markdown_with_single_quick_menu_button(self):
        message = FakeMessage()

        response = await reply_with_quick_menu(message, "**模型回复**")

        self.assertEqual(response, {"id": "reply-id"})
        self.assertEqual(len(message.reply_calls), 1)
        call = message.reply_calls[0]
        self.assertEqual(call["msg_type"], 2)
        self.assertEqual(call["markdown"], {"content": "**模型回复**"})
        self.assertEqual(call["msg_seq"], 1)
        rows = call["keyboard"]["content"]["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["buttons"][0]["action"]["data"], "/菜单")

    async def test_reply_passes_full_original_url_to_copy_button(self):
        message = FakeMessage()
        full_reply_url = "https://example.com/aiqq/reply/random-token.txt"

        await reply_with_quick_menu(
            message,
            "主人，简要结论，喵。",
            full_reply_url=full_reply_url,
        )

        reply = message.reply_calls[0]
        self.assertEqual(reply["markdown"]["content"], "主人，简要结论，喵。")
        rows = reply["keyboard"]["content"]["rows"]
        self.assertEqual(rows[1]["buttons"][0]["action"]["data"], full_reply_url)

    async def test_reply_can_mention_group_user_without_message_reference(self):
        message = FakeMessage()

        await reply_with_quick_menu(
            message,
            "主人，回复内容，喵。",
            mention_user_openid="member-openid-1",
        )

        reply = message.reply_calls[0]
        self.assertEqual(
            reply["markdown"]["content"],
            "<@member-openid-1> 主人，回复内容，喵。",
        )
        self.assertNotIn("message_reference", reply)

    async def test_long_reply_adds_quick_menu_only_to_final_message(self):
        message = FakeMessage()

        await reply_with_quick_menu(
            message,
            "A" * 15 + "\n\n" + "B" * 15,
            msg_seq=3,
            max_chars=18,
        )

        self.assertEqual(len(message.reply_calls), 2)
        first, final = message.reply_calls
        self.assertEqual(first["msg_seq"], 3)
        self.assertEqual(first["markdown"]["content"], "A" * 15)
        self.assertNotIn("keyboard", first)
        self.assertEqual(final["msg_seq"], 4)
        self.assertEqual(final["markdown"]["content"], "B" * 15)
        self.assertIn("keyboard", final)

    async def test_long_reply_sends_plain_parts_and_keeps_menu_on_final(self):
        message = FakeMessage()

        await reply_with_quick_menu(
            message,
            "A" * 80,
            msg_seq=3,
            max_chars=60,
        )

        self.assertEqual(len(message.reply_calls), 2)
        first, final = message.reply_calls
        self.assertEqual(first["markdown"]["content"], "A" * 60)
        self.assertNotIn("keyboard", first)
        self.assertEqual(final["markdown"]["content"], "A" * 20)
        self.assertIn("keyboard", final)

    async def test_unsplit_long_reply_uses_one_plain_message(self):
        message = FakeMessage()
        content = "A" * 80

        await reply_with_quick_menu(
            message,
            content,
            max_chars=20,
            split_content=False,
        )

        self.assertEqual(len(message.reply_calls), 1)
        reply = message.reply_calls[0]
        self.assertEqual(reply["markdown"]["content"], content)
        self.assertIn("keyboard", reply)

    async def test_feature_menu_reply_displays_suggestion_when_requested(self):
        message = FakeMessage()

        await reply_feature_menu(
            message,
            novelai_suggestion="mountain lake, sunrise",
        )

        rows = message.reply_calls[0]["keyboard"]["content"]["rows"]
        self.assertEqual(len(rows), 5)
        self.assertEqual(
            rows[4]["buttons"][0]["action"]["data"],
            "NovelAI生图 mountain lake, sunrise",
        )

    async def test_generated_prompt_reply_has_action_only_on_final_part(self):
        message = FakeMessage()

        prompts = (
            "white cat, safe, sfw",
            "white cat, sunlight, safe, sfw",
            "white cat, portrait, safe, sfw",
        )
        await reply_with_novelai_prompt_options(
            message,
            "A" * 80,
            prompts,
            "session-token",
            max_chars=60,
        )

        self.assertEqual(len(message.reply_calls), 2)
        first, final = message.reply_calls
        self.assertNotIn("keyboard", first)
        rows = final["keyboard"]["content"]["rows"]
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(len(row["buttons"]) == 1 for row in rows))
        self.assertEqual(
            rows[0]["buttons"][0]["action"]["data"],
            "NovelAI生图 white cat, safe, sfw",
        )
        self.assertEqual(rows[3]["buttons"][0]["render_data"]["label"], "修改提示词")

    async def test_research_stage_uses_markdown_without_keyboard(self):
        message = FakeMessage()

        await reply_research_stage(
            message,
            "目前资料主要指向接口超时问题。",
            msg_seq=3,
        )

        self.assertEqual(
            message.reply_calls,
            [
                {
                    "msg_type": 2,
                    "markdown": {"content": "目前资料主要指向接口超时问题。"},
                    "msg_seq": 3,
                }
            ],
        )

    async def test_research_stage_over_50_characters_stays_plain(self):
        message = FakeMessage()

        await reply_research_stage(message, "A" * 51, msg_seq=3)

        call = message.reply_calls[0]
        self.assertEqual(call["markdown"]["content"], "A" * 51)
        self.assertNotIn("keyboard", call)


if __name__ == "__main__":
    unittest.main()
