import unittest
from types import SimpleNamespace

from ai_service import (
    ImagePromptAuditResult,
    NovelAIPromptOptionsResult,
    normalize_reply_summary,
)
from bot import AiQQBot
from gpt_image_service import GPTGeneratedImage, GPTImageError
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
    def __init__(self, audit):
        self.audit = audit
        self.audit_calls = []
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

    async def summarize_reply(self, content, user_prompt=""):
        self.summary_calls.append((content, user_prompt))
        return normalize_reply_summary(content)

    async def audit_image_prompt(self, prompt, *, require_english=False):
        self.audit_calls.append((prompt, require_english))
        return self.audit

    async def create_novelai_prompts(self, description):
        self.created_prompt_calls.append(description)
        return self.created_prompt_result

    async def revise_novelai_prompts(self, prompts, request):
        self.revised_prompt_calls.append((tuple(prompts), request))
        return self.revised_prompt_result


class FakeNovelAI:
    is_configured = True
    is_ready = True

    def __init__(self):
        self.prompts = []
        self.orientations = []
        self.error = None

    async def generate(self, prompt, *, orientation="square"):
        self.prompts.append(prompt)
        self.orientations.append(orientation)
        if self.error is not None:
            raise self.error
        return GeneratedImage(b"image", "image/png")


class FakeGPTImage:
    is_configured = True
    model = "gpt-image-2"

    def __init__(self):
        self.prompts = []
        self.error = None

    async def generate(self, prompt):
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return GPTGeneratedImage(b"gpt-image", "image/png")


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


def make_audit(
    *,
    safe=True,
    effective=True,
    available=True,
    contains_chinese=False,
    category="safe",
    reason="valid",
    suggested_prompt="",
    orientation="square",
):
    return ImagePromptAuditResult(
        safe,
        effective,
        available,
        contains_chinese,
        category,
        reason,
        suggested_prompt,
        orientation,
    )


def make_bot(audit=None):
    audit = audit or make_audit()
    bot = SimpleNamespace(
        ai=FakeAI(audit),
        gpt_image=FakeGPTImage(),
        novelai=FakeNovelAI(),
        image_usage=FakeUsage(),
        media_store=FakeMediaStore(),
        reply_store=FakeReplyStore(),
        prompt_sessions=FakePromptSessions(),
    )
    bot.prepare_novelai_prompt = AiQQBot.prepare_novelai_prompt.__get__(bot)
    bot.revise_novelai_prompt = AiQQBot.revise_novelai_prompt.__get__(bot)
    bot.generate_gpt_image = AiQQBot.generate_gpt_image.__get__(bot)
    return bot


class BotImageWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_command_returns_three_options_through_ai_session(self):
        bot = make_bot()
        message = FakeMessage(
            "/NovelAI提示词 一位站在樱花树下的白发少女"
        )

        await AiQQBot.reply_with_context(
            bot, message, "group:1:member:1"
        )

        self.assertEqual(
            bot.ai.created_prompt_calls,
            ["一位站在樱花树下的白发少女"],
        )
        self.assertEqual(bot.novelai.prompts, [])
        self.assertEqual(bot.image_usage.success_keys, [])

        reply = message.reply_calls[0]
        self.assertLessEqual(len(reply["markdown"]["content"]), 50)
        self.assertNotIn("```text", reply["markdown"]["content"])
        self.assertIn("方案1", bot.reply_store.saved[-1])
        self.assertIn(PROMPT_OPTIONS[0], bot.reply_store.saved[-1])
        self.assertEqual(
            bot.ai.summary_calls[0][1],
            "一位站在樱花树下的白发少女",
        )
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
        bot = make_bot()
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
        self.assertEqual(bot.ai.summary_calls[0][1], "方案2改成夜景")
        rows = message.reply_calls[0]["keyboard"]["content"]["rows"]
        self.assertIn("night city", rows[0]["buttons"][0]["action"]["data"])
        self.assertIn("new-session-token", rows[3]["buttons"][0]["action"]["data"])

    async def test_prompt_edit_rejects_expired_or_foreign_session(self):
        bot = make_bot()
        message = FakeMessage(
            "修改NovelAI提示词 invalid-token 改成夜景"
        )

        await AiQQBot.reply_with_context(
            bot, message, "group:1:member:1"
        )

        self.assertEqual(bot.ai.revised_prompt_calls, [])
        self.assertIn("这次修改已经过期", message.reply_calls[0]["markdown"]["content"])
        self.assertIn("喵", message.reply_calls[0]["markdown"]["content"])

    async def test_unsafe_prompt_request_is_not_generated_or_recorded(self):
        bot = make_bot(
            make_audit(
                safe=False,
                category="adult_content",
                reason="包含成人内容。",
                suggested_prompt="adult woman, fully clothed, safe, sfw",
            )
        )
        message = FakeMessage("NovelAI提示词 不安全的成人画面")

        await AiQQBot.reply_with_context(
            bot, message, "group:1:member:1"
        )

        self.assertEqual(bot.ai.created_prompt_calls, [])
        self.assertIn("原因：包含成人内容", message.reply_calls[0]["markdown"]["content"])

    async def test_empty_prompt_returns_reason_without_retry_payload(self):
        bot = make_bot()
        message = FakeMessage()

        await AiQQBot.generate_novelai_image(
            bot, message, "group:1:member:1", ""
        )

        reply = message.reply_calls[0]
        self.assertIn("原因：没有", reply["markdown"]["content"])
        rows = reply["keyboard"]["content"]["rows"]
        self.assertEqual(len(rows), 3)
        button = rows[0]["buttons"][0]
        self.assertEqual(button["render_data"]["label"], "功能菜单")
        self.assertEqual(button["action"]["data"], "/菜单")
        suggestion = rows[1]["buttons"][0]
        self.assertEqual(suggestion["render_data"]["label"], "🟢 使用建议")
        self.assertIn("NovelAI生图 peaceful mountain lake", suggestion["action"]["data"])

    async def test_unsafe_prompt_never_calls_novelai(self):
        bot = make_bot(
            make_audit(
                safe=False,
                category="adult_content",
                reason="提示词包含成人内容。",
                suggested_prompt=(
                    "adult woman, fully clothed, city street, safe, sfw"
                ),
            )
        )
        message = FakeMessage()

        await AiQQBot.generate_novelai_image(
            bot, message, "group:1:member:1", "unsafe prompt"
        )

        self.assertEqual(bot.novelai.prompts, [])
        self.assertEqual(bot.ai.audit_calls, [("unsafe prompt", True)])
        self.assertEqual(len(message.reply_calls), 1)
        reply = message.reply_calls[0]
        self.assertIn("原因：提示词包含成人内容", reply["markdown"]["content"])
        self.assertIn("fully clothed", bot.reply_store.saved[-1])
        rows = reply["keyboard"]["content"]["rows"]
        self.assertEqual(len(rows), 3)
        menu_button = rows[0]["buttons"][0]
        self.assertEqual(menu_button["render_data"]["label"], "功能菜单")
        self.assertEqual(menu_button["action"]["data"], "/菜单")
        suggestion = rows[1]["buttons"][0]
        self.assertEqual(suggestion["render_data"]["label"], "🟢 使用建议")
        self.assertIn("NovelAI生图 adult woman, fully clothed", suggestion["action"]["data"])

    async def test_safe_prompt_generates_and_returns_image(self):
        bot = make_bot()
        message = FakeMessage()

        await AiQQBot.generate_novelai_image(
            bot, message, "group:1:member:1", "safe landscape"
        )

        self.assertEqual(bot.novelai.prompts, ["safe landscape"])
        self.assertEqual(bot.novelai.orientations, ["square"])
        self.assertEqual(bot.ai.audit_calls, [("safe landscape", True)])
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

    async def test_full_body_audit_uses_portrait_novelai_image(self):
        bot = make_bot(make_audit(orientation="portrait"))
        message = FakeMessage()

        await AiQQBot.generate_novelai_image(
            bot, message, "group:1:member:1", "1girl, full body"
        )

        self.assertEqual(bot.novelai.orientations, ["portrait"])

    async def test_lying_pose_audit_uses_landscape_novelai_image(self):
        bot = make_bot(make_audit(orientation="landscape"))
        message = FakeMessage()

        await AiQQBot.generate_novelai_image(
            bot, message, "group:1:member:1", "1girl, lying down"
        )

        self.assertEqual(bot.novelai.orientations, ["landscape"])

    async def test_chinese_prompt_returns_english_suggestion_without_generation(self):
        audit = make_audit(
            contains_chinese=True,
            reason="包含中文",
            suggested_prompt="white cat, sitting by a window",
        )
        bot = make_bot(audit)
        message = FakeMessage()

        await AiQQBot.generate_novelai_image(
            bot, message, "group:1:member:1", "一只坐在窗边的白猫"
        )

        self.assertEqual(bot.novelai.prompts, [])
        self.assertEqual(bot.image_usage.success_keys, [])
        reply = message.reply_calls[0]["markdown"]["content"]
        self.assertIn("原因：包含中文", reply)
        self.assertIn("white cat", bot.reply_store.saved[-1])
        rows = message.reply_calls[0]["keyboard"]["content"]["rows"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["buttons"][0]["render_data"]["label"], "功能菜单")
        self.assertEqual(rows[0]["buttons"][0]["render_data"]["style"], 0)
        suggestion = rows[1]["buttons"][0]
        self.assertEqual(suggestion["render_data"]["label"], "🟢 使用建议")
        self.assertEqual(
            suggestion["action"]["data"],
            "NovelAI生图 white cat, sitting by a window",
        )
        self.assertFalse(suggestion["action"]["enter"])

    async def test_ineffective_prompt_never_calls_novelai(self):
        audit = make_audit(
            effective=False,
            reason="没有画面内容",
            suggested_prompt="mountain lake, sunrise",
        )
        bot = make_bot(audit)
        message = FakeMessage()

        await AiQQBot.generate_novelai_image(
            bot, message, "group:1:member:1", "do it"
        )

        self.assertEqual(bot.novelai.prompts, [])
        response = message.reply_calls[0]
        reply = response["markdown"]["content"]
        self.assertIn("原因：没有画面内容", reply)
        self.assertIn("mountain lake", bot.reply_store.saved[-1])
        rows = response["keyboard"]["content"]["rows"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            rows[1]["buttons"][0]["action"]["data"],
            "NovelAI生图 mountain lake, sunrise",
        )

    async def test_unavailable_safety_check_returns_reason_and_suggestion_button(self):
        bot = make_bot(
            make_audit(
                safe=False,
                effective=False,
                available=False,
                category="service_unavailable",
                reason="安全审核服务暂时不可用。",
                suggested_prompt="peaceful mountain lake, sunrise, safe, sfw",
            )
        )
        message = FakeMessage()

        await AiQQBot.generate_novelai_image(
            bot, message, "group:1:member:1", "landscape"
        )

        response = message.reply_calls[0]
        self.assertIn("原因：安全审核服务暂时不可用", response["markdown"]["content"])
        button = response["keyboard"]["content"]["rows"][1]["buttons"][0]
        self.assertEqual(button["render_data"]["label"], "🟢 使用建议")
        self.assertEqual(
            button["action"]["data"],
            "NovelAI生图 peaceful mountain lake, sunrise, safe, sfw",
        )

    async def test_unavailable_unified_audit_returns_reason_and_suggestion_button(self):
        audit = make_audit(
            safe=False,
            effective=False,
            available=False,
            category="service_unavailable",
            reason="图片提示词审核暂时不可用。",
            suggested_prompt="peaceful mountain lake, sunrise, safe, sfw",
        )
        bot = make_bot(audit)
        message = FakeMessage()

        await AiQQBot.generate_novelai_image(
            bot, message, "group:1:member:1", "landscape"
        )

        response = message.reply_calls[0]
        self.assertIn("原因：图片提示词审核暂时不可用", response["markdown"]["content"])
        button = response["keyboard"]["content"]["rows"][1]["buttons"][0]
        self.assertEqual(button["render_data"]["label"], "🟢 使用建议")
        self.assertEqual(
            button["action"]["data"],
            "NovelAI生图 peaceful mountain lake, sunrise, safe, sfw",
        )

    async def test_safe_prompt_sends_c2c_image_as_qq_media(self):
        bot = make_bot()
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
        bot = make_bot()
        bot.novelai.error = NovelAIError("timeout")
        message = FakeMessage()

        await AiQQBot.generate_novelai_image(
            bot, message, "group:1:member:1", "safe landscape"
        )

        self.assertEqual(bot.image_usage.success_keys, [])
        self.assertEqual(bot.image_usage.finished_keys, ["group:1:member:1"])
        self.assertIn(
            "画笔卡住", message.reply_calls[1]["markdown"]["content"]
        )

    async def test_gpt_image_command_accepts_chinese_and_returns_qq_image(self):
        bot = make_bot()
        message = FakeMessage("/GPT生图 雨夜霓虹街道上的白猫")

        await AiQQBot.reply_with_context(
            bot, message, "group:1:member:1"
        )

        self.assertEqual(bot.ai.audit_calls, [("雨夜霓虹街道上的白猫", False)])
        self.assertEqual(bot.gpt_image.prompts, ["雨夜霓虹街道上的白猫"])
        self.assertEqual(bot.novelai.prompts, [])
        self.assertEqual(bot.image_usage.success_keys, ["group:1:member:1"])
        self.assertEqual(bot.image_usage.finished_keys, ["group:1:member:1"])
        self.assertEqual(len(message.reply_calls), 2)
        self.assertIn("GPT 正在画图", message.reply_calls[0]["markdown"]["content"])
        self.assertEqual(message.reply_calls[1]["msg_type"], 7)
        self.assertEqual(
            message.reply_calls[1]["media"], {"file_info": "qq-file-info"}
        )

    async def test_unsafe_gpt_prompt_is_rejected_before_generation(self):
        bot = make_bot(
            make_audit(
                safe=False,
                category="adult_content",
                reason="提示词包含成人内容。",
                suggested_prompt="fully clothed adult, city street, safe, sfw",
            )
        )
        message = FakeMessage("GPT生图 unsafe request")

        await AiQQBot.reply_with_context(
            bot, message, "group:1:member:1"
        )

        self.assertEqual(bot.gpt_image.prompts, [])
        self.assertEqual(bot.image_usage.success_keys, [])
        self.assertIn("原因：提示词包含成人内容", bot.reply_store.saved[-1])
        rows = message.reply_calls[0]["keyboard"]["content"]["rows"]
        suggestion = rows[1]["buttons"][0]
        self.assertEqual(suggestion["render_data"]["label"], "🟢 使用建议")
        self.assertEqual(
            suggestion["action"]["data"],
            "GPT生图 fully clothed adult, city street, safe, sfw",
        )

    async def test_failed_gpt_generation_releases_global_slot(self):
        bot = make_bot()
        bot.gpt_image.error = GPTImageError("timeout")
        message = FakeMessage()

        await AiQQBot.generate_gpt_image(
            bot, message, "group:1:member:1", "safe landscape"
        )

        self.assertEqual(bot.image_usage.success_keys, [])
        self.assertEqual(bot.image_usage.finished_keys, ["group:1:member:1"])
        self.assertIn("GPT 的画笔卡住", message.reply_calls[1]["markdown"]["content"])


if __name__ == "__main__":
    unittest.main()
