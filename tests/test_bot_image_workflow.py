import unittest
from types import SimpleNamespace

from ai_service import (
    ImagePromptQualityResult,
    NovelAIPromptOptionsResult,
    PromptSafetyResult,
    normalize_reply_summary,
)
from bot import AiQQBot
from image_generation import UsageDecision
from media_store import MediaAsset
from novelai_service import GeneratedImage, NovelAIError
from prompt_sessions import PromptSession


PROMPT_OPTIONS = (
    "1girl, white hair, cherry blossoms, safe, sfw, fully clothed",
    "1girl, white hair, sunset, safe, sfw, fully clothed",
    "1girl, white hair, portrait, safe, sfw, fully clothed",
)


class FakeMessage:
    def __init__(self, content="", *, c2c=False):
        self.content = content
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
        self.total_timeout_seconds = 270
        self.max_reply_chars = 3000
        self.created_prompt_calls = []
        self.revised_prompt_calls = []
        self.created_prompt_result = NovelAIPromptOptionsResult(
            True, PROMPT_OPTIONS
        )
        self.revised_prompt_result = NovelAIPromptOptionsResult(
            True,
            tuple(option.replace("cherry blossoms", "night city") for option in PROMPT_OPTIONS),
        )
        self.summary_calls = []

    async def summarize_reply(self, content):
        self.summary_calls.append(content)
        return normalize_reply_summary(content)

    async def moderate_image_prompt(self, prompt):
        self.prompts.append(prompt)
        return self.moderation

    async def review_image_prompt(self, prompt):
        self.review_prompts.append(prompt)
        return self.review

    async def create_novelai_prompts(
        self, description, *, history=(), summary=""
    ):
        self.created_prompt_calls.append((description, list(history), summary))
        return self.created_prompt_result

    async def revise_novelai_prompts(
        self, prompts, request, *, history=(), summary=""
    ):
        self.revised_prompt_calls.append(
            (tuple(prompts), request, list(history), summary)
        )
        return self.revised_prompt_result


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


class FakeReplyStore:
    def __init__(self):
        self.saved = []

    def save(self, content):
        self.saved.append(content)
        return SimpleNamespace(
            public_url="https://example.com/aiqq/reply/random-token.txt"
        )


class FakeContext:
    summary = "用户喜欢柔和光线。"

    def model_history(self):
        return [{"role": "user", "content": "使用日系插画风格"}]


class FakeConversations:
    def __init__(self):
        self.recorded = []
        self.summarized = []

    async def load_context(self, key):
        return FakeContext()

    async def record_round(self, key, user_content, assistant_content):
        self.recorded.append((key, user_content, assistant_content))

    async def summarize_if_needed(self, key):
        self.summarized.append(key)


class FakePromptSessions:
    def __init__(self):
        self.created = []
        self.loaded = None

    async def create(self, conversation_key, prompts):
        self.created.append((conversation_key, tuple(prompts)))
        return PromptSession("new-session-token", tuple(prompts))

    async def load(self, conversation_key, token):
        self.load_call = (conversation_key, token)
        return self.loaded


def make_bot(moderation, review=None):
    if review is None:
        review = ImagePromptQualityResult(True, True, False, "", "valid")
    bot = SimpleNamespace(
        ai=FakeAI(moderation, review),
        novelai=FakeNovelAI(),
        image_usage=FakeUsage(),
        media_store=FakeMediaStore(),
        reply_store=FakeReplyStore(),
        conversations=FakeConversations(),
        prompt_sessions=FakePromptSessions(),
    )
    bot.prepare_novelai_prompt = AiQQBot.prepare_novelai_prompt.__get__(bot)
    bot.revise_novelai_prompt = AiQQBot.revise_novelai_prompt.__get__(bot)
    return bot


class BotImageWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_command_returns_three_options_and_records_history(self):
        bot = make_bot(PromptSafetyResult(True, True, "safe"))
        message = FakeMessage(
            "/NovelAI提示词 一位站在樱花树下的白发少女"
        )

        await AiQQBot.reply_with_context(
            bot, message, "group:1:member:1"
        )

        self.assertEqual(len(bot.ai.created_prompt_calls), 1)
        description, history, summary = bot.ai.created_prompt_calls[0]
        self.assertEqual(description, "一位站在樱花树下的白发少女")
        self.assertEqual(history[0]["content"], "使用日系插画风格")
        self.assertEqual(summary, "用户喜欢柔和光线。")
        self.assertEqual(bot.novelai.prompts, [])
        self.assertEqual(bot.image_usage.success_keys, [])
        self.assertEqual(len(bot.conversations.recorded), 1)
        recorded = bot.conversations.recorded[0]
        self.assertIn("NovelAI提示词 一位站在樱花树下", recorded[1])
        self.assertIn("方案1", recorded[2])
        self.assertIn(PROMPT_OPTIONS[0], recorded[2])
        self.assertEqual(bot.conversations.summarized, ["group:1:member:1"])

        reply = message.reply_calls[0]
        self.assertLessEqual(len(reply["markdown"]["content"]), 30)
        self.assertNotIn("```text", reply["markdown"]["content"])
        self.assertIn("方案1", bot.reply_store.saved[-1])
        self.assertIn(PROMPT_OPTIONS[0], bot.reply_store.saved[-1])
        rows = reply["keyboard"]["content"]["rows"]
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(len(row["buttons"]) == 1 for row in rows))
        self.assertEqual(
            rows[0]["buttons"][0]["action"]["data"],
            f"NovelAI生图 {PROMPT_OPTIONS[0]}",
        )
        self.assertEqual(rows[3]["buttons"][0]["render_data"]["label"], "修改提示词")
        self.assertEqual(
            rows[3]["buttons"][0]["action"]["data"],
            "修改NovelAI提示词 new-session-token ",
        )
        self.assertEqual(rows[4]["buttons"][0]["render_data"]["label"], "查看完整输出")

    async def test_prompt_edit_uses_session_and_records_revised_options(self):
        bot = make_bot(PromptSafetyResult(True, True, "safe"))
        bot.prompt_sessions.loaded = PromptSession(
            "old-session-token", PROMPT_OPTIONS
        )
        message = FakeMessage(
            "修改NovelAI提示词 old-session-token 方案2改成夜景"
        )

        await AiQQBot.reply_with_context(
            bot, message, "group:1:member:1"
        )

        self.assertEqual(bot.prompt_sessions.load_call, ("group:1:member:1", "old-session-token"))
        call = bot.ai.revised_prompt_calls[0]
        self.assertEqual(call[0], PROMPT_OPTIONS)
        self.assertEqual(call[1], "方案2改成夜景")
        self.assertEqual(len(bot.conversations.recorded), 1)
        self.assertIn("修改NovelAI提示词 方案2改成夜景", bot.conversations.recorded[0][1])
        self.assertIn("night city", bot.conversations.recorded[0][2])
        rows = message.reply_calls[0]["keyboard"]["content"]["rows"]
        self.assertIn("night city", rows[0]["buttons"][0]["action"]["data"])
        self.assertIn("new-session-token", rows[3]["buttons"][0]["action"]["data"])

    async def test_prompt_edit_rejects_expired_or_foreign_session(self):
        bot = make_bot(PromptSafetyResult(True, True, "safe"))
        message = FakeMessage(
            "修改NovelAI提示词 invalid-token 改成夜景"
        )

        await AiQQBot.reply_with_context(
            bot, message, "group:1:member:1"
        )

        self.assertEqual(bot.ai.revised_prompt_calls, [])
        self.assertEqual(bot.conversations.recorded, [])
        self.assertIn("不存在或已超过 15 分钟", message.reply_calls[0]["markdown"]["content"])

    async def test_unsafe_prompt_request_is_not_generated_or_recorded(self):
        bot = make_bot(
            PromptSafetyResult(
                False,
                True,
                "adult_content",
                "包含成人内容。",
                "adult woman, fully clothed, safe, sfw",
            )
        )
        message = FakeMessage("NovelAI提示词 不安全的成人画面")

        await AiQQBot.reply_with_context(
            bot, message, "group:1:member:1"
        )

        self.assertEqual(bot.ai.created_prompt_calls, [])
        self.assertEqual(bot.conversations.recorded, [])
        self.assertIn("拦截原因：包含成人内容", message.reply_calls[0]["markdown"]["content"])

    async def test_empty_prompt_returns_reason_without_retry_payload(self):
        bot = make_bot(PromptSafetyResult(True, True, "safe"))
        message = FakeMessage()

        await AiQQBot.generate_novelai_image(
            bot, message, "group:1:member:1", ""
        )

        reply = message.reply_calls[0]
        self.assertIn("拦截原因：没有", reply["markdown"]["content"])
        rows = reply["keyboard"]["content"]["rows"]
        self.assertEqual(len(rows), 2)
        button = rows[0]["buttons"][0]
        self.assertEqual(button["render_data"]["label"], "功能菜单")
        self.assertEqual(button["action"]["data"], "/菜单")

    async def test_unsafe_prompt_never_calls_novelai(self):
        bot = make_bot(
            PromptSafetyResult(
                False,
                True,
                "adult_content",
                "提示词包含成人内容。",
                "adult woman, fully clothed, city street, safe, sfw",
            )
        )
        message = FakeMessage()

        await AiQQBot.generate_novelai_image(
            bot, message, "group:1:member:1", "unsafe prompt"
        )

        self.assertEqual(bot.novelai.prompts, [])
        self.assertEqual(bot.ai.review_prompts, [])
        self.assertEqual(len(message.reply_calls), 1)
        reply = message.reply_calls[0]
        self.assertIn("拦截原因：提示词包含成人内容", reply["markdown"]["content"])
        self.assertIn("fully clothed", bot.reply_store.saved[-1])
        rows = reply["keyboard"]["content"]["rows"]
        self.assertEqual(len(rows), 2)
        menu_button = rows[0]["buttons"][0]
        self.assertEqual(menu_button["render_data"]["label"], "功能菜单")
        self.assertEqual(
            menu_button["action"]["data"],
            "/菜单 NovelAI提示词 unsafe prompt",
        )

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
        self.assertIn("拦截原因：包含中文", reply)
        self.assertIn("white cat", bot.reply_store.saved[-1])
        rows = message.reply_calls[0]["keyboard"]["content"]["rows"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["buttons"][0]["render_data"]["label"], "功能菜单")
        self.assertEqual(rows[0]["buttons"][0]["render_data"]["style"], 0)
        self.assertEqual(
            rows[0]["buttons"][0]["action"]["data"],
            "/菜单 NovelAI提示词 一只坐在窗边的白猫",
        )
        self.assertTrue(rows[0]["buttons"][0]["action"]["enter"])

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
        response = message.reply_calls[0]
        reply = response["markdown"]["content"]
        self.assertIn("拦截原因：没有画面内容", reply)
        self.assertIn("mountain lake", bot.reply_store.saved[-1])
        rows = response["keyboard"]["content"]["rows"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            rows[0]["buttons"][0]["action"]["data"],
            "/菜单 NovelAI提示词 do it",
        )

    async def test_unavailable_safety_check_returns_reason_and_suggestion_button(self):
        bot = make_bot(
            PromptSafetyResult(
                False,
                False,
                "service_unavailable",
                "安全审核服务暂时不可用。",
                "peaceful mountain lake, sunrise, safe, sfw",
            )
        )
        message = FakeMessage()

        await AiQQBot.generate_novelai_image(
            bot, message, "group:1:member:1", "landscape"
        )

        response = message.reply_calls[0]
        self.assertIn("拦截原因：安全审核服务暂时不可用", response["markdown"]["content"])
        button = response["keyboard"]["content"]["rows"][0]["buttons"][0]
        self.assertEqual(button["render_data"]["label"], "功能菜单")
        self.assertEqual(
            button["action"]["data"],
            "/菜单 NovelAI提示词 landscape",
        )

    async def test_unavailable_quality_check_returns_reason_and_suggestion_button(self):
        review = ImagePromptQualityResult(
            False,
            False,
            False,
            "peaceful mountain lake, sunrise, safe, sfw",
            "提示词有效性检查暂时不可用。",
        )
        bot = make_bot(PromptSafetyResult(True, True, "safe"), review)
        message = FakeMessage()

        await AiQQBot.generate_novelai_image(
            bot, message, "group:1:member:1", "landscape"
        )

        response = message.reply_calls[0]
        self.assertIn("拦截原因：提示词有效性检查暂时不可用", response["markdown"]["content"])
        button = response["keyboard"]["content"]["rows"][0]["buttons"][0]
        self.assertEqual(button["render_data"]["label"], "功能菜单")
        self.assertEqual(
            button["action"]["data"],
            "/菜单 NovelAI提示词 landscape",
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
