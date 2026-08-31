import unittest
from types import SimpleNamespace

from ai_service import ImagePromptQualityResult, PromptSafetyResult
from bot import AiQQBot
from image_generation import UsageDecision
from media_store import MediaAsset
from novelai_service import GeneratedImage, NovelAIError


class FakeMessage:
    def __init__(self, *, c2c=False):
        self.reply_calls = []
        self._api = FakeAPI()
        if c2c:
            self.author = SimpleNamespace(user_openid="user-1")
        else:
            self.group_openid = "group-1"

    async def reply(self, **kwargs):
        self.reply_calls.append(kwargs)


class FakeAPI:
    def __init__(self):
        self.group_file_calls = []
        self.c2c_file_calls = []

    async def post_group_file(self, **kwargs):
        self.group_file_calls.append(kwargs)
        return {"file_info": "qq-file-info"}

    async def post_c2c_file(self, **kwargs):
        self.c2c_file_calls.append(kwargs)
        return {"file_info": "qq-c2c-file-info"}


class FakeAI:
    def __init__(self, moderation, review):
        self.moderation = moderation
        self.review = review
        self.prompts = []
        self.review_prompts = []

    async def moderate_image_prompt(self, prompt):
        self.prompts.append(prompt)
        return self.moderation

    async def review_image_prompt(self, prompt):
        self.review_prompts.append(prompt)
        return self.review


class FakeNovelAI:
    is_configured = True
    is_ready = True

    def __init__(self):
        self.prompts = []
        self.error = None

    async def generate(self, prompt):
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return GeneratedImage(b"image", "image/png")


class FakeUsage:
    def __init__(self):
        self.success_keys = []
        self.finished_keys = []

    async def reserve_attempt(self, _key):
        return UsageDecision(True)

    async def record_success(self, key):
        self.success_keys.append(key)

    async def finish_attempt(self, key):
        self.finished_keys.append(key)


class FakeMediaStore:
    def save(self, _image):
        return MediaAsset("image.png", "https://example.com/aiqq/media/image.png")


def make_bot(moderation, review=None):
    if review is None:
        review = ImagePromptQualityResult(True, True, False, "", "valid")
    return SimpleNamespace(
        ai=FakeAI(moderation, review),
        novelai=FakeNovelAI(),
        image_usage=FakeUsage(),
        media_store=FakeMediaStore(),
    )


class BotImageWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_unsafe_prompt_never_calls_novelai(self):
        bot = make_bot(PromptSafetyResult(False, True, "adult_content"))
        message = FakeMessage()

        await AiQQBot.generate_novelai_image(
            bot, message, "group:1:member:1", "unsafe prompt"
        )

        self.assertEqual(bot.novelai.prompts, [])
        self.assertEqual(bot.ai.review_prompts, [])
        self.assertEqual(len(message.reply_calls), 1)
        self.assertIn("请求未生成", message.reply_calls[0]["markdown"]["content"])

    async def test_safe_prompt_generates_and_returns_image(self):
        bot = make_bot(PromptSafetyResult(True, True, "safe"))
        message = FakeMessage()

        await AiQQBot.generate_novelai_image(
            bot, message, "group:1:member:1", "safe landscape"
        )

        self.assertEqual(bot.novelai.prompts, ["safe landscape"])
        self.assertEqual(bot.image_usage.success_keys, ["group:1:member:1"])
        self.assertEqual(bot.image_usage.finished_keys, ["group:1:member:1"])
        self.assertEqual(len(message.reply_calls), 2)
        self.assertEqual(message.reply_calls[0]["msg_seq"], 1)
        self.assertEqual(message.reply_calls[1]["msg_seq"], 2)
        self.assertEqual(message.reply_calls[1]["msg_type"], 7)
        self.assertEqual(
            message.reply_calls[1]["media"], {"file_info": "qq-file-info"}
        )
        self.assertEqual(
            message._api.group_file_calls,
            [
                {
                    "group_openid": "group-1",
                    "file_type": 1,
                    "url": "https://example.com/aiqq/media/image.png",
                    "srv_send_msg": False,
                }
            ],
        )

    async def test_chinese_prompt_returns_english_suggestion_without_generation(self):
        review = ImagePromptQualityResult(
            True,
            True,
            True,
            "white cat, sitting by a window",
            "包含中文",
        )
        bot = make_bot(PromptSafetyResult(True, True, "safe"), review)
        message = FakeMessage()

        await AiQQBot.generate_novelai_image(
            bot, message, "group:1:member:1", "一只坐在窗边的白猫"
        )

        self.assertEqual(bot.novelai.prompts, [])
        self.assertEqual(bot.image_usage.success_keys, [])
        reply = message.reply_calls[0]["markdown"]["content"]
        self.assertIn("包含中文", reply)
        self.assertIn("white cat", reply)
        rows = message.reply_calls[0]["keyboard"]["content"]["rows"]
        self.assertEqual(
            rows[2]["buttons"][0]["render_data"]["label"], "🟢 使用建议"
        )
        self.assertEqual(rows[2]["buttons"][0]["render_data"]["style"], 0)
        self.assertEqual(
            rows[2]["buttons"][0]["action"]["data"],
            "NovelAI生图 white cat, sitting by a window",
        )
        self.assertFalse(rows[2]["buttons"][0]["action"]["enter"])

    async def test_ineffective_prompt_never_calls_novelai(self):
        review = ImagePromptQualityResult(
            False,
            True,
            False,
            "mountain lake, sunrise",
            "没有画面内容",
        )
        bot = make_bot(PromptSafetyResult(True, True, "safe"), review)
        message = FakeMessage()

        await AiQQBot.generate_novelai_image(
            bot, message, "group:1:member:1", "do it"
        )

        self.assertEqual(bot.novelai.prompts, [])
        reply = message.reply_calls[0]["markdown"]["content"]
        self.assertIn("缺少明确", reply)
        self.assertIn("mountain lake", reply)
        rows = message.reply_calls[0]["keyboard"]["content"]["rows"]
        self.assertEqual(
            rows[2]["buttons"][0]["render_data"]["label"], "🟢 使用建议"
        )

    async def test_safe_prompt_sends_c2c_image_as_qq_media(self):
        bot = make_bot(PromptSafetyResult(True, True, "safe"))
        message = FakeMessage(c2c=True)

        await AiQQBot.generate_novelai_image(
            bot, message, "c2c:user-1", "safe landscape"
        )

        self.assertEqual(len(message.reply_calls), 2)
        self.assertEqual(message.reply_calls[1]["msg_type"], 7)
        self.assertEqual(message.reply_calls[1]["msg_seq"], 2)
        self.assertEqual(
            message.reply_calls[1]["media"],
            {"file_info": "qq-c2c-file-info"},
        )
        self.assertEqual(
            message._api.c2c_file_calls,
            [
                {
                    "openid": "user-1",
                    "file_type": 1,
                    "url": "https://example.com/aiqq/media/image.png",
                    "srv_send_msg": False,
                }
            ],
        )

    async def test_failed_generation_releases_global_slot(self):
        bot = make_bot(PromptSafetyResult(True, True, "safe"))
        bot.novelai.error = NovelAIError("timeout")
        message = FakeMessage()

        await AiQQBot.generate_novelai_image(
            bot, message, "group:1:member:1", "safe landscape"
        )

        self.assertEqual(bot.image_usage.success_keys, [])
        self.assertEqual(bot.image_usage.finished_keys, ["group:1:member:1"])
        self.assertIn(
            "生图失败", message.reply_calls[1]["markdown"]["content"]
        )


if __name__ == "__main__":
    unittest.main()
