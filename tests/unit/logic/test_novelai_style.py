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


if __name__ == "__main__":
    unittest.main()
