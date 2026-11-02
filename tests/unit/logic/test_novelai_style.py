import asyncio
import unittest
from unittest.mock import AsyncMock

from aiqq.logic.novelai_style import NovelAIStyleWorkflow


class NovelAIStyleWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_preserves_weighted_tags_and_internal_whitespace(self):
        styles = AsyncMock()
        workflow = NovelAIStyleWorkflow(styles=styles)
        result = await workflow.run(
            "  {{watercolor}},\n1.2::soft  lighting:: \n", member_openid="member"
        )
        self.assertEqual(result.status, "ok")
        self.assertIn("保存并启用", result.full_text)
        styles.save.assert_awaited_once_with(
            "member", "{{watercolor}},\n1.2::soft  lighting::"
        )
        styles.load.assert_not_awaited()
        self.assertEqual(result.images, ())

    async def test_empty_input_clears_style(self):
        for prompt in ("", " \n\t "):
            with self.subTest(prompt=prompt):
                styles = AsyncMock()
                result = await NovelAIStyleWorkflow(styles=styles).run(
                    prompt, member_openid="member"
                )
                styles.save.assert_awaited_once_with("member", "")
                self.assertEqual(result.status, "ok")
                self.assertIn("已清除", result.full_text)

    async def test_non_ascii_style_does_not_overwrite_existing_style(self):
        styles = AsyncMock()
        result = await NovelAIStyleWorkflow(styles=styles).run(
            "水彩", member_openid="member"
        )
        self.assertEqual(result.status, "rejected")
        self.assertIn("必须使用英文", result.full_text)
        styles.save.assert_not_awaited()

    async def test_copied_line_markers_zero_width_and_markdown_escapes_are_cleaned(self):
        styles = AsyncMock()
        result = await NovelAIStyleWorkflow(styles=styles).run(
            "\u200b\\-3::artist collaboration::, ↵ 1\\.6::artist:channel \\(caststation\\)::",
            member_openid="member",
        )
        self.assertEqual(result.status, "ok")
        styles.save.assert_awaited_once_with(
            "member", "-3::artist collaboration::, \n 1.6::artist:channel (caststation)::"
        )

    async def test_storage_failure_never_claims_save_or_clear_succeeded(self):
        for prompt, failure in (("secret-style", "保存失败"), ("", "清除失败")):
            with self.subTest(operation=failure):
                styles = AsyncMock()
                styles.save.side_effect = RuntimeError("private-member secret-style")
                with self.assertLogs("aiqq.logic.novelai_style") as logs:
                    result = await NovelAIStyleWorkflow(styles=styles).run(
                        prompt, member_openid="private-member"
                    )
                self.assertEqual(result.status, "unavailable")
                self.assertEqual(result.error_code, "novelai_style_unavailable")
                self.assertIn(failure, result.full_text)
                self.assertIn("稍后", result.full_text)
                self.assertNotIn("private-member", "\n".join(logs.output))
                self.assertNotIn("secret-style", "\n".join(logs.output))

    async def test_cancellation_propagates(self):
        styles = AsyncMock()
        styles.save.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await NovelAIStyleWorkflow(styles=styles).run(
                "watercolor", member_openid="member"
            )

    async def test_v5_style_save_clear_and_validation_name_its_own_provider(self):
        styles = AsyncMock()
        workflow = NovelAIStyleWorkflow(
            styles=styles, provider_name="Nai v5",
            command_name="Nai v5 画风", provider_id="nai_v5",
        )
        prompt = "{{pixel art}},\nsoft  lighting"
        saved = await workflow.run(f"  {prompt} \n", member_openid="member")
        self.assertEqual(saved.status, "ok")
        self.assertIn("Nai v5", saved.summary)
        self.assertIn("/Nai v5 画风", saved.full_text)
        self.assertNotIn("NovelAI", saved.full_text)
        styles.save.assert_awaited_once_with("member", prompt)

        styles.save.reset_mock()
        rejected = await workflow.run("水彩", member_openid="member")
        self.assertEqual(rejected.status, "rejected")
        self.assertIn("Nai v5", rejected.full_text)
        styles.save.assert_not_awaited()

        cleared = await workflow.run(" \n ", member_openid="member")
        self.assertEqual(cleared.status, "ok")
        self.assertIn("已清除你的 Nai v5 画风", cleared.summary)
        styles.save.assert_awaited_once_with("member", "")
        styles.load.assert_not_awaited()

    async def test_v5_storage_failure_reports_own_command_without_private_data(self):
        for prompt, failure in (("secret-style", "保存失败"), ("", "清除失败")):
            with self.subTest(operation=failure):
                styles = AsyncMock()
                styles.save.side_effect = RuntimeError("private-member secret-style")
                workflow = NovelAIStyleWorkflow(
                    styles=styles, provider_name="Nai v5",
                    command_name="Nai v5 画风", provider_id="nai_v5",
                )
                with self.assertLogs("aiqq.logic.novelai_style") as logs:
                    result = await workflow.run(prompt, member_openid="private-member")
                self.assertEqual(result.status, "unavailable")
                self.assertEqual(result.error_code, "nai_v5_style_unavailable")
                self.assertEqual(result.summary, result.full_text)
                self.assertIn(failure, result.full_text)
                self.assertIn("/Nai v5 画风", result.full_text)
                self.assertNotIn("NovelAI", result.full_text)
                self.assertNotIn("private-member", "\n".join(logs.output))
                self.assertNotIn("secret-style", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
