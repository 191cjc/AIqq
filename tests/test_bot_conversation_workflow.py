import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ai_service import (
    AIResult,
    ChatPromptSafetyResult,
    GroupReferenceImageSelectionResult,
    normalize_reply_summary,
)
from bot import (
    AiQQBot,
    full_group_event_mentions_bot,
    full_group_message_data,
)
from codex_image import CodexGeneratedImage
from image_generation import (
    RecentImageRequestStore,
    UsageDecision,
)
from media_store import MediaAsset
from recorded_group_message import RecordedGroupMessage


class FakeAPI:
    def __init__(self):
        self.group_file_calls = []

    async def post_group_file(self, **kwargs):
        self.group_file_calls.append(kwargs)
        return {"file_info": "qq-codex-image"}


class FakeMessage:
    content = "查询最新接口限制"

    def __init__(self, content=None, *, group_member_openid=None):
        if content is not None:
            self.content = content
        if group_member_openid is not None:
            self.id = "source-message-id"
            self.author = SimpleNamespace(member_openid=group_member_openid)
        self.group_openid = "group-1"
        self._api = FakeAPI()
        self.reply_calls = []

    async def reply(self, **kwargs):
        self.reply_calls.append(kwargs)
        return {"id": f"reply-{len(self.reply_calls)}"}


class FakeAI:
    def __init__(
        self,
        moderation=None,
        total_timeout_seconds=270,
        max_reply_chars=3000,
        summary_text=None,
        response_text="最终完整回答。",
        response_images=(),
        response_image_urls=(),
        response_image_prompt=None,
        image_output_enabled=False,
        answer_delay=0,
        reference_selection_index=1,
        reference_selection_available=True,
        reference_selection_reason="已匹配图片记录",
    ):
        self.total_timeout_seconds = total_timeout_seconds
        self.max_reply_chars = max_reply_chars
        self.moderation = moderation or ChatPromptSafetyResult(
            True, True, "safe", "普通内容"
        )
        self.moderation_calls = []
        self.moderation_context_calls = []
        self.moderation_reference_context_calls = []
        self.moderation_group_history_context_calls = []
        self.summary_calls = []
        self.summary_text = summary_text
        self.response_text = response_text
        self.response_images = tuple(response_images)
        self.response_image_urls = tuple(response_image_urls)
        self.response_image_prompt = response_image_prompt
        self.answer_delay = answer_delay
        self.answer_calls = []
        self.answer_reference_calls = []
        self.answer_history_calls = []
        self.answer_history_image_calls = []
        self.reference_selection_index = reference_selection_index
        self.reference_selection_available = reference_selection_available
        self.reference_selection_reason = reference_selection_reason
        self.reference_selection_calls = []
        self.clear_calls = 0
        self.image_output_enabled = image_output_enabled

    async def moderate_chat_prompt(
        self,
        prompt,
        *,
        image_context_available=False,
        reference_image_available=False,
        group_history_available=False,
    ):
        self.moderation_calls.append(prompt)
        self.moderation_context_calls.append(image_context_available)
        self.moderation_reference_context_calls.append(reference_image_available)
        self.moderation_group_history_context_calls.append(
            group_history_available
        )
        return self.moderation

    async def answer(
        self,
        prompt,
        *,
        on_stage=None,
        enable_image_generation=False,
        use_reference_image=False,
        group_message_history=(),
        group_history_images=(),
    ):
        self.answer_calls.append((prompt, enable_image_generation))
        self.answer_reference_calls.append(use_reference_image)
        self.answer_history_calls.append(group_message_history)
        self.answer_history_image_calls.append(group_history_images)
        if self.answer_delay:
            await asyncio.sleep(self.answer_delay)
        if on_stage is not None:
            await on_stage("目前资料主要指向接口超时问题。")
        return AIResult(
            self.response_text,
            True,
            summary=self.summary_text or normalize_reply_summary(self.response_text),
            images=self.response_images,
            image_urls=self.response_image_urls,
            image_prompt=self.response_image_prompt,
        )

    async def select_group_reference_image(self, prompt, candidates):
        self.reference_selection_calls.append((prompt, candidates))
        return GroupReferenceImageSelectionResult(
            self.reference_selection_index,
            self.reference_selection_available,
            self.reference_selection_reason,
        )

    async def clear_chat_session(self):
        self.clear_calls += 1
        return True

    async def summarize_reply(self, full_text, user_prompt=""):
        self.summary_calls.append((full_text, user_prompt))
        if self.summary_text is not None and len(full_text) > 50:
            return self.summary_text
        return normalize_reply_summary(full_text)


class FakeReplyStore:
    def __init__(self):
        self.saved = []

    def save(self, content):
        self.saved.append(content)
        return SimpleNamespace(
            public_url="https://example.com/aiqq/reply/random-token.txt"
        )


class FakeImageUsage:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.reserved = []
        self.succeeded = []
        self.finished = []

    async def reserve_attempt(self, key):
        self.reserved.append(key)
        return UsageDecision(self.allowed, "已有图片正在生成，请稍后再试。")

    async def record_success(self, key):
        self.succeeded.append(key)

    async def finish_attempt(self, key):
        self.finished.append(key)


class FakeMediaStore:
    def __init__(self):
        self.saved = []

    def save(self, image):
        self.saved.append(image)
        return MediaAsset(
            "codex-image.png",
            "https://example.com/aiqq/media/codex-image.png",
        )


class FakeGPTImage:
    is_configured = True

    def __init__(self, image):
        self.image = image
        self.prompts = []
        self.edits = []

    async def generate(self, prompt):
        self.prompts.append(prompt)
        return self.image

    async def edit(self, prompt, reference_data, reference_mime_type):
        self.edits.append((prompt, reference_data, reference_mime_type))
        return self.image


class FakeWebImages:
    def __init__(self, image=None):
        self.image = image
        self.calls = []

    async def download_best(self, candidates):
        self.calls.append(candidates)
        return self.image


class BrokenWebImages:
    async def download_best(self, _candidates):
        raise RuntimeError("download failed")


class FakeGroupMessages:
    def __init__(self, messages=(), error=None):
        self.messages = tuple(messages)
        self.error = error
        self.calls = []
        self.image_calls = []
        self.get_calls = []

    async def recent(self, group_openid, **kwargs):
        self.calls.append((group_openid, kwargs))
        if self.error is not None:
            raise self.error
        return self.messages

    async def recent_images(self, group_openid, **kwargs):
        self.image_calls.append((group_openid, kwargs))
        if self.error is not None:
            raise self.error
        return self.messages

    async def get_message(self, group_openid, message_id):
        self.get_calls.append((group_openid, message_id))
        if self.error is not None:
            raise self.error
        return next(
            (
                message
                for message in self.messages
                if getattr(message, "message_id", "") == message_id
            ),
            None,
        )


class BotConversationWorkflowTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def stored_group_message(
        content,
        *,
        username="小明",
        sent_at="2026-09-08T15:00:00+08:00",
        payload=None,
        message_id="stored-message",
        record_id=1,
        member_openid="member-1",
    ):
        return SimpleNamespace(
            record_id=record_id,
            username=username,
            member_openid=member_openid,
            sent_at=sent_at,
            content=content,
            message_type=0,
            payload=payload or {},
            message_id=message_id,
        )

    async def test_menu_command_opens_previous_button_list_without_ai_call(self):
        ai = FakeAI()
        bot = SimpleNamespace(
            ai=ai,
            reply_store=FakeReplyStore(),
        )
        message = FakeMessage("/菜单")

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(ai.moderation_calls, [])
        self.assertEqual(ai.answer_calls, [])
        rows = message.reply_calls[0]["keyboard"]["content"]["rows"]
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["buttons"][0]["render_data"]["label"], "清除记忆")
        self.assertEqual(rows[1]["buttons"][0]["render_data"]["label"], "NovelAI提示词")
        self.assertEqual(rows[2]["buttons"][0]["render_data"]["label"], "NovelAI生图")
        self.assertEqual(rows[3]["buttons"][0]["render_data"]["label"], "GPT生图")
        self.assertEqual(ai.summary_calls, [])

    async def test_menu_command_restores_carried_image_suggestion(self):
        bot = SimpleNamespace(
            ai=FakeAI(),
            reply_store=FakeReplyStore(),
        )
        message = FakeMessage("/菜单 white cat, morning sunlight")

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        rows = message.reply_calls[0]["keyboard"]["content"]["rows"]
        self.assertEqual(len(rows), 5)
        suggestion = rows[4]["buttons"][0]
        self.assertEqual(suggestion["render_data"]["label"], "🟢 使用建议")
        self.assertEqual(
            suggestion["action"]["data"],
            "NovelAI生图 white cat, morning sunlight",
        )

    async def test_menu_command_restores_original_prompt_request(self):
        bot = SimpleNamespace(
            ai=FakeAI(),
            reply_store=FakeReplyStore(),
        )
        message = FakeMessage("/菜单 NovelAI提示词 一只坐在窗边的白猫")

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        rows = message.reply_calls[0]["keyboard"]["content"]["rows"]
        self.assertEqual(len(rows), 5)
        button = rows[4]["buttons"][0]
        self.assertEqual(button["render_data"]["label"], "🟢 生成提示词方案")
        self.assertEqual(
            button["action"]["data"],
            "NovelAI提示词 一只坐在窗边的白猫",
        )

    async def test_stage_has_no_buttons_and_final_reply_uses_next_sequence(self):
        ai = FakeAI()
        reply_store = FakeReplyStore()
        bot = SimpleNamespace(
            ai=ai,
            reply_store=reply_store,
        )
        message = FakeMessage(group_member_openid="member-openid-1")

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(len(message.reply_calls), 2)
        stage, final = message.reply_calls
        self.assertEqual(stage["msg_seq"], 1)
        self.assertNotIn("keyboard", stage)
        self.assertNotIn("message_reference", stage)
        self.assertNotIn("<@", stage["markdown"]["content"])
        self.assertEqual(final["msg_seq"], 2)
        self.assertIn("keyboard", final)
        self.assertEqual(
            final["markdown"]["content"],
            "<@member-openid-1> 最终完整回答。",
        )
        self.assertNotIn("message_reference", final)
        self.assertEqual(
            len(final["keyboard"]["content"]["rows"]),
            1,
        )
        self.assertEqual(reply_store.saved, [])
        self.assertEqual(ai.summary_calls, [])
        self.assertEqual(ai.moderation_calls, ["查询最新接口限制"])
        self.assertEqual(ai.answer_calls, [("查询最新接口限制", False)])

    async def test_group_history_request_queries_current_group_and_passes_safe_data(self):
        ai = FakeAI(
            moderation=ChatPromptSafetyResult(
                True,
                True,
                "safe",
                "查询当前群记录",
                message_history_request=True,
            ),
            response_text="主人，刚才主要在讨论测试安排喵。",
        )
        group_messages = FakeGroupMessages(
            (
                self.stored_group_message(
                    "第二条消息",
                    username="小红",
                    sent_at="2026-09-08T15:02:00+08:00",
                    payload={
                        "author": {"member_openid": "secret-openid"},
                        "attachments": [
                            {
                                "filename": "result.png",
                                "content_type": "image/png",
                                "width": 512,
                                "height": 512,
                                "url": "https://secret.example/result.png",
                            }
                        ],
                        "message_scene": {"ext": ["auth_token=secret"]},
                    },
                ),
                self.stored_group_message(
                    "第一条消息",
                    username="小明",
                    sent_at="2026-09-08T15:01:00+08:00",
                ),
            )
        )
        bot = SimpleNamespace(
            ai=ai,
            reply_store=FakeReplyStore(),
            group_messages=group_messages,
        )
        message = FakeMessage("刚才群里聊了什么", group_member_openid="member-1")

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(
            group_messages.calls,
            [
                (
                    "group-1",
                    {"limit": 50, "exclude_message_id": "source-message-id"},
                )
            ],
        )
        self.assertEqual(ai.moderation_group_history_context_calls, [True])
        history = ai.answer_history_calls[0]
        self.assertEqual([item["content"] for item in history], ["第一条消息", "第二条消息"])
        self.assertEqual(history[1]["sender"], "小红")
        self.assertEqual(
            history[1]["attachments"],
            [
                {
                    "filename": "result.png",
                    "content_type": "image/png",
                    "width": 512,
                    "height": 512,
                }
            ],
        )
        serialized = str(history)
        self.assertNotIn("secret-openid", serialized)
        self.assertNotIn("https://secret.example", serialized)
        self.assertNotIn("auth_token", serialized)

    async def test_group_history_image_is_downloaded_and_forwarded_for_vision(self):
        image = CodexGeneratedImage(b"qq-image", "image/jpeg")
        image_url = "https://multimedia.nt.qq.com.cn/download?id=secret"
        ai = FakeAI(
            moderation=ChatPromptSafetyResult(
                True,
                True,
                "safe",
                "查看刚才群图片",
                message_history_request=True,
                group_history_image_request=True,
            ),
            response_text="主人，图上写着发布成功喵。",
        )
        group_messages = FakeGroupMessages(
            (
                self.stored_group_message(
                    "",
                    message_id="image-message",
                    payload={
                        "attachments": [
                            {
                                "filename": "status.jpg",
                                "content_type": "image/jpeg",
                                "width": 1920,
                                "height": 1080,
                                "url": image_url,
                            }
                        ]
                    },
                ),
            )
        )
        web_images = FakeWebImages(image)
        bot = SimpleNamespace(
            ai=ai,
            reply_store=FakeReplyStore(),
            group_messages=group_messages,
            web_images=web_images,
        )
        message = FakeMessage(
            "我刚刚发了一个图片，上面讲了什么",
            group_member_openid="member-1",
        )

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(web_images.calls, [(image_url,)])
        self.assertEqual(ai.answer_history_image_calls, [(image,)])
        history = ai.answer_history_calls[0]
        self.assertTrue(history[0]["attachments"][0]["vision_input"])
        self.assertNotIn(image_url, str(history))

    async def test_group_history_image_request_reports_missing_image(self):
        ai = FakeAI(
            moderation=ChatPromptSafetyResult(
                True,
                True,
                "safe",
                "查看刚才群图片",
                message_history_request=True,
                group_history_image_request=True,
            )
        )
        bot = SimpleNamespace(
            ai=ai,
            reply_store=FakeReplyStore(),
            group_messages=FakeGroupMessages(
                (self.stored_group_message("只有文字"),)
            ),
            web_images=FakeWebImages(),
        )
        message = FakeMessage("看看上面的图片", group_member_openid="member-1")

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(ai.answer_calls, [])
        self.assertIn("没找到可读取的图片", message.reply_calls[0]["markdown"]["content"])

    async def test_full_group_event_with_bot_mention_reuses_reply_flow(self):
        payload = {
            "id": "gateway-event",
            "d": {
                "id": "message-1",
                "group_openid": "group-1",
                "content": "<@bot-openid> @浴火AI猫娘助手 图片说了什么",
                "author": {"member_openid": "member-1"},
                "mentions": [
                    {
                        "is_you": True,
                        "bot": True,
                        "username": "浴火AI猫娘助手",
                    }
                ],
            },
        }
        store = SimpleNamespace(save_gateway_event=AsyncMock(return_value=True))
        reply_once = AsyncMock()
        bot = SimpleNamespace(
            group_messages=store,
            api=FakeAPI(),
            _reply_to_group_message_once=reply_once,
        )

        await AiQQBot.on_group_message_observed(
            bot, "GROUP_MESSAGE_CREATE", payload
        )

        self.assertTrue(full_group_event_mentions_bot(payload))
        self.assertEqual(
            full_group_message_data(payload)["content"], "图片说了什么"
        )
        message = reply_once.await_args.args[0]
        self.assertEqual(message.content, "图片说了什么")
        self.assertEqual(message.author.member_openid, "member-1")

    async def test_full_group_event_without_bot_mention_is_only_saved(self):
        payload = {
            "id": "gateway-event",
            "d": {
                "id": "message-1",
                "group_openid": "group-1",
                "content": "普通群消息",
                "author": {"member_openid": "member-1"},
            },
        }
        reply_once = AsyncMock()
        bot = SimpleNamespace(
            group_messages=SimpleNamespace(
                save_gateway_event=AsyncMock(return_value=True)
            ),
            api=FakeAPI(),
            _reply_to_group_message_once=reply_once,
        )

        await AiQQBot.on_group_message_observed(
            bot, "GROUP_MESSAGE_CREATE", payload
        )

        reply_once.assert_not_awaited()

    async def test_duplicate_group_message_is_replied_to_once(self):
        reply_with_context = AsyncMock()
        bot = SimpleNamespace(
            _replied_group_message_ids={},
            reply_with_context=reply_with_context,
            group_messages=SimpleNamespace(),
            robot=SimpleNamespace(name="浴火AI猫娘助手"),
        )
        message = SimpleNamespace(
            id="message-1",
            group_openid="group-1",
            author=SimpleNamespace(member_openid="member-1"),
        )

        await AiQQBot._reply_to_group_message_once(bot, message)
        await AiQQBot._reply_to_group_message_once(bot, message)

        reply_with_context.assert_awaited_once()
        recorded, conversation_key = reply_with_context.await_args.args
        self.assertIsInstance(recorded, RecordedGroupMessage)
        self.assertIs(recorded._message, message)
        self.assertEqual(conversation_key, "group:group-1:member:member-1")

    async def test_empty_group_history_returns_message_without_ai_answer(self):
        ai = FakeAI(
            moderation=ChatPromptSafetyResult(
                True,
                True,
                "safe",
                "查询当前群记录",
                message_history_request=True,
            )
        )
        group_messages = FakeGroupMessages()
        bot = SimpleNamespace(
            ai=ai,
            reply_store=FakeReplyStore(),
            group_messages=group_messages,
        )
        message = FakeMessage("总结最近聊天", group_member_openid="member-1")

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(ai.answer_calls, [])
        self.assertIn("还没有可查询的消息记录", message.reply_calls[0]["markdown"]["content"])

    async def test_group_history_database_failure_returns_friendly_error(self):
        ai = FakeAI(
            moderation=ChatPromptSafetyResult(
                True,
                True,
                "safe",
                "查询当前群记录",
                message_history_request=True,
            )
        )
        group_messages = FakeGroupMessages(error=RuntimeError("database busy"))
        bot = SimpleNamespace(
            ai=ai,
            reply_store=FakeReplyStore(),
            group_messages=group_messages,
        )
        message = FakeMessage("查一下前面的消息", group_member_openid="member-1")

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(ai.answer_calls, [])
        self.assertIn("群消息记录暂时翻不开", message.reply_calls[0]["markdown"]["content"])

    async def test_normal_chat_does_not_query_group_history_database(self):
        ai = FakeAI(response_text="主人，可以这样处理喵。")
        group_messages = FakeGroupMessages(error=AssertionError("must not query"))
        bot = SimpleNamespace(
            ai=ai,
            reply_store=FakeReplyStore(),
            group_messages=group_messages,
        )
        message = FakeMessage("这个接口怎么用", group_member_openid="member-1")

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(group_messages.calls, [])
        self.assertEqual(ai.answer_history_calls, [()])

    async def test_long_final_answer_is_summarized_with_full_reply_button(self):
        answer = "A" * 40 + "\n\n" + "B" * 40
        ai = FakeAI(
            max_reply_chars=18,
            summary_text="主人，该接口当前上限为3000字，喵。",
            response_text=answer,
        )
        reply_store = FakeReplyStore()
        bot = SimpleNamespace(
            ai=ai,
            reply_store=reply_store,
        )
        message = FakeMessage()

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(len(message.reply_calls), 2)
        stage, final = message.reply_calls
        self.assertEqual(stage["msg_seq"], 1)
        self.assertNotIn("keyboard", stage)
        self.assertEqual(final["msg_seq"], 2)
        visible_text = final["markdown"]["content"]
        self.assertLessEqual(len(visible_text), 50)
        self.assertEqual(visible_text, "主人，该接口当前上限为3000字，喵。")
        self.assertEqual(ai.summary_calls, [])
        self.assertEqual(reply_store.saved, [answer])
        rows = final["keyboard"]["content"]["rows"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["buttons"][0]["render_data"]["label"], "查看完整输出")
        self.assertEqual(
            rows[1]["buttons"][0]["action"]["data"],
            "https://example.com/aiqq/reply/random-token.txt",
        )

    async def test_contextual_gpt_image_is_uploaded_after_final_text(self):
        image = CodexGeneratedImage(b"png-image", "image/png")
        image_prompt = "white cat, rainy neon street, cinematic lighting"
        ai = FakeAI(
            moderation=ChatPromptSafetyResult(
                True,
                True,
                "safe",
                "安全生图请求",
                image_request=True,
            ),
            response_text="主人，白猫图片画好啦，喵。",
            response_image_prompt=image_prompt,
            response_image_urls=("https://images.example/search-result.jpg",),
            image_output_enabled=True,
        )
        usage = FakeImageUsage()
        media_store = FakeMediaStore()
        gpt_image = FakeGPTImage(image)
        web_images = FakeWebImages(CodexGeneratedImage(b"web", "image/jpeg"))
        bot = SimpleNamespace(
            ai=ai,
            gpt_image=gpt_image,
            reply_store=FakeReplyStore(),
            image_usage=usage,
            recent_image_requests=RecentImageRequestStore(),
            media_store=media_store,
            web_images=web_images,
        )
        message = FakeMessage("请生成一张雨夜白猫图片")

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(ai.answer_calls[-1][1], True)
        self.assertEqual(len(message.reply_calls), 3)
        self.assertIn("白猫图片画好", message.reply_calls[1]["markdown"]["content"])
        self.assertEqual(message.reply_calls[2]["msg_type"], 7)
        self.assertEqual(message.reply_calls[2]["msg_seq"], 3)
        self.assertEqual(media_store.saved, [image])
        self.assertEqual(gpt_image.prompts, [image_prompt])
        self.assertEqual(usage.succeeded, ["group:1:member:1"])
        self.assertEqual(usage.finished, ["group:1:member:1"])
        self.assertEqual(web_images.calls, [])
        self.assertEqual(
            message._api.group_file_calls[0]["url"],
            "https://example.com/aiqq/media/codex-image.png",
        )

    async def test_reference_image_request_uses_ai_decision_and_image_edit_api(self):
        selected_url = "https://multimedia.nt.qq.com.cn/record-21.jpg"
        edited = CodexGeneratedImage(b"edited-image", "image/jpeg")
        ai = FakeAI(
            moderation=ChatPromptSafetyResult(
                True,
                True,
                "safe",
                "使用刚才图片进行修改",
                image_request=True,
                use_reference_image=True,
            ),
            response_text="主人，正在按刚才的图修改喵。",
            response_image_prompt="Preserve the reference character, make her smile",
            image_output_enabled=True,
        )
        gpt_image = FakeGPTImage(edited)
        group_messages = FakeGroupMessages(
            (
                self.stored_group_message(
                    "较新的图片",
                    record_id=22,
                    username="小红",
                    member_openid="member-2",
                    payload={
                        "attachments": [
                            {
                                "content_type": "image/png",
                                "filename": "newer.png",
                                "url": "https://multimedia.nt.qq.com.cn/record-22.png",
                            }
                        ]
                    },
                ),
                self.stored_group_message(
                    "要修改的角色图",
                    record_id=21,
                    payload={
                        "attachments": [
                            {
                                "content_type": "image/jpeg",
                                "filename": "character.jpg",
                                "url": selected_url,
                            }
                        ]
                    },
                ),
            )
        )
        ai.reference_selection_index = 2
        bot = SimpleNamespace(
            ai=ai,
            gpt_image=gpt_image,
            reply_store=FakeReplyStore(),
            image_usage=FakeImageUsage(),
            recent_image_requests=RecentImageRequestStore(),
            media_store=FakeMediaStore(),
            web_images=FakeWebImages(
                CodexGeneratedImage(b"reference-image", "image/jpeg")
            ),
            group_messages=group_messages,
        )

        await AiQQBot.reply_with_context(
            bot,
            FakeMessage(
                "用消息记录 21 的图片进行图生图，改成微笑",
                group_member_openid="member-1",
            ),
            "group:1:member:1",
        )

        self.assertEqual(ai.moderation_reference_context_calls, [True])
        self.assertEqual(ai.answer_reference_calls, [True])
        self.assertEqual(group_messages.image_calls[0][0], "group-1")
        selection_candidates = ai.reference_selection_calls[0][1]
        self.assertEqual(
            [item["record_id"] for item in selection_candidates], [22, 21]
        )
        self.assertNotIn("https://", str(selection_candidates))
        self.assertEqual(bot.web_images.calls, [(selected_url,)])
        self.assertEqual(gpt_image.prompts, [])
        self.assertEqual(
            gpt_image.edits,
            [
                (
                    "Preserve the reference character, make her smile",
                    b"reference-image",
                    "image/jpeg",
                )
            ],
        )

    async def test_replied_image_is_used_before_newer_history_image(self):
        quoted_url = "https://multimedia.nt.qq.com.cn/quoted.jpg"
        newer_url = "https://multimedia.nt.qq.com.cn/newer.jpg"
        reference = CodexGeneratedImage(b"quoted-image", "image/jpeg")
        edited = CodexGeneratedImage(b"edited-image", "image/jpeg")
        ai = FakeAI(
            moderation=ChatPromptSafetyResult(
                True,
                True,
                "safe",
                "使用回复图片进行修改",
                image_request=True,
                use_reference_image=True,
            ),
            response_text="主人，正在按回复的图片修改喵。",
            response_image_prompt="Preserve the reference, add meme text",
            image_output_enabled=True,
        )
        current_reply = self.stored_group_message(
            "用这张图生成表情包",
            message_id="source-message-id",
            record_id=37,
            payload={
                "msg_elements": [
                    {
                        "message_type": 103,
                        "attachments": [
                            {
                                "content_type": "image/jpeg",
                                "filename": "quoted.jpg",
                                "url": quoted_url,
                            }
                        ],
                    }
                ]
            },
        )
        newer_image = self.stored_group_message(
            "更新的图片",
            message_id="newer-message",
            record_id=38,
            payload={
                "attachments": [
                    {
                        "content_type": "image/jpeg",
                        "filename": "newer.jpg",
                        "url": newer_url,
                    }
                ]
            },
        )
        group_messages = FakeGroupMessages((newer_image, current_reply))
        web_images = FakeWebImages(reference)
        gpt_image = FakeGPTImage(edited)
        bot = SimpleNamespace(
            ai=ai,
            gpt_image=gpt_image,
            reply_store=FakeReplyStore(),
            image_usage=FakeImageUsage(),
            recent_image_requests=RecentImageRequestStore(),
            media_store=FakeMediaStore(),
            web_images=web_images,
            group_messages=group_messages,
        )
        message = FakeMessage(
            "用这张图生成一个表情包",
            group_member_openid="member-1",
        )

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(
            group_messages.get_calls,
            [("group-1", "source-message-id")],
        )
        self.assertEqual(group_messages.image_calls, [])
        self.assertEqual(ai.reference_selection_calls, [])
        self.assertEqual(web_images.calls, [(quoted_url,)])
        self.assertEqual(
            gpt_image.edits,
            [("Preserve the reference, add meme text", b"quoted-image", "image/jpeg")],
        )

    async def test_reference_image_request_without_group_record_is_explained(self):
        ai = FakeAI(
            moderation=ChatPromptSafetyResult(
                True,
                True,
                "safe",
                "请求使用刚才的图片",
                image_request=True,
                use_reference_image=True,
            ),
            image_output_enabled=True,
        )
        usage = FakeImageUsage()
        gpt_image = FakeGPTImage(CodexGeneratedImage(b"unused", "image/jpeg"))
        bot = SimpleNamespace(
            ai=ai,
            gpt_image=gpt_image,
            reply_store=FakeReplyStore(),
            image_usage=usage,
            recent_image_requests=RecentImageRequestStore(),
            media_store=FakeMediaStore(),
            web_images=FakeWebImages(),
            group_messages=FakeGroupMessages(),
        )
        message = FakeMessage("用刚才的图重画")

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(ai.answer_calls, [])
        self.assertEqual(usage.reserved, [])
        self.assertIn("消息记录里还没有", message.reply_calls[0]["markdown"]["content"])

    async def test_ambiguous_group_reference_image_requests_more_detail(self):
        ai = FakeAI(
            moderation=ChatPromptSafetyResult(
                True,
                True,
                "safe",
                "请求使用群图片",
                image_request=True,
                use_reference_image=True,
            ),
            image_output_enabled=True,
            reference_selection_index=0,
            reference_selection_reason="有两张图片都符合，请补充记录号。",
        )
        image_message = self.stored_group_message(
            "",
            payload={
                "attachments": [
                    {
                        "content_type": "image/jpeg",
                        "filename": "photo.jpg",
                        "url": "https://multimedia.nt.qq.com.cn/photo.jpg",
                    }
                ]
            },
        )
        bot = SimpleNamespace(
            ai=ai,
            gpt_image=FakeGPTImage(CodexGeneratedImage(b"unused", "image/jpeg")),
            reply_store=FakeReplyStore(),
            image_usage=FakeImageUsage(),
            recent_image_requests=RecentImageRequestStore(),
            media_store=FakeMediaStore(),
            web_images=FakeWebImages(),
            group_messages=FakeGroupMessages((image_message,)),
        )
        message = FakeMessage("用之前那张图重画", group_member_openid="member-1")

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(ai.answer_calls, [])
        self.assertIn("不能确定", message.reply_calls[0]["markdown"]["content"])
        self.assertIn("补充记录号", message.reply_calls[0]["markdown"]["content"])

    async def test_recent_image_context_enables_followup_but_not_image_questions(self):
        class ContextAwareFakeAI(FakeAI):
            async def moderate_chat_prompt(
                self,
                prompt,
                *,
                image_context_available=False,
                reference_image_available=False,
                group_history_available=False,
            ):
                self.moderation_calls.append(prompt)
                self.moderation_context_calls.append(image_context_available)
                self.moderation_reference_context_calls.append(
                    reference_image_available
                )
                self.moderation_group_history_context_calls.append(
                    group_history_available
                )
                image_request = prompt.startswith("生成一张") or (
                    image_context_available and prompt == "图呢"
                )
                return ChatPromptSafetyResult(
                    True,
                    True,
                    "safe",
                    "安全内容",
                    image_request=image_request,
                )

        image = CodexGeneratedImage(b"png-image", "image/png")
        ai = ContextAwareFakeAI(
            response_text="主人，正在生成图片，喵。",
            response_image_prompt="white cat, rainy street",
            image_output_enabled=True,
        )
        bot = SimpleNamespace(
            ai=ai,
            gpt_image=FakeGPTImage(image),
            reply_store=FakeReplyStore(),
            image_usage=FakeImageUsage(),
            recent_image_requests=RecentImageRequestStore(),
            media_store=FakeMediaStore(),
            web_images=FakeWebImages(),
        )

        await AiQQBot.reply_with_context(
            bot,
            FakeMessage("生成一张白猫图片"),
            "group:1:member:1",
        )
        await AiQQBot.reply_with_context(
            bot,
            FakeMessage("图呢"),
            "group:1:member:1",
        )
        await AiQQBot.reply_with_context(
            bot,
            FakeMessage("图片格式是什么"),
            "group:1:member:1",
        )
        await AiQQBot.reply_with_context(
            bot,
            FakeMessage("图呢"),
            "group:1:member:2",
        )

        self.assertEqual(
            ai.moderation_context_calls,
            [False, True, True, False],
        )
        self.assertEqual(
            [enabled for _, enabled in ai.answer_calls],
            [True, True, False, False],
        )
        self.assertEqual(len(bot.gpt_image.prompts), 2)
        self.assertEqual(
            bot.image_usage.reserved,
            ["group:1:member:1", "group:1:member:1"],
        )

    async def test_search_image_is_downloaded_and_sent_after_final_text(self):
        url = "https://images.example/fuji.jpg"
        image = CodexGeneratedImage(b"web-image", "image/jpeg")
        ai = FakeAI(
            response_text="主人，这是富士山的资料，喵。",
            response_image_urls=(url,),
        )
        usage = FakeImageUsage()
        media_store = FakeMediaStore()
        web_images = FakeWebImages(image)
        bot = SimpleNamespace(
            ai=ai,
            reply_store=FakeReplyStore(),
            image_usage=usage,
            media_store=media_store,
            web_images=web_images,
        )
        message = FakeMessage("查一下富士山并配一张图")

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(len(message.reply_calls), 3)
        self.assertIn("富士山", message.reply_calls[1]["markdown"]["content"])
        self.assertEqual(message.reply_calls[2]["msg_type"], 7)
        self.assertEqual(message.reply_calls[2]["msg_seq"], 3)
        self.assertEqual(web_images.calls, [(url,)])
        self.assertEqual(media_store.saved, [image])
        self.assertEqual(usage.reserved, [])
        self.assertEqual(usage.succeeded, [])
        self.assertEqual(usage.finished, [])

    async def test_search_image_failure_does_not_add_a_noisy_chat_message(self):
        ai = FakeAI(
            response_text="主人，文字资料仍可正常查看，喵。",
            response_image_urls=("https://images.example/broken.jpg",),
        )
        bot = SimpleNamespace(
            ai=ai,
            reply_store=FakeReplyStore(),
            media_store=FakeMediaStore(),
            web_images=BrokenWebImages(),
        )
        message = FakeMessage("查询资料并配图")

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(len(message.reply_calls), 2)
        self.assertIn("文字资料", message.reply_calls[-1]["markdown"]["content"])

    async def test_exactly_50_character_answer_skips_summary_and_full_reply(self):
        answer = "答" * 50
        ai = FakeAI(summary_text="不应调用", response_text=answer)
        reply_store = FakeReplyStore()
        bot = SimpleNamespace(
            ai=ai,
            reply_store=reply_store,
        )
        message = FakeMessage()

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        final = message.reply_calls[-1]
        self.assertEqual(final["markdown"]["content"], answer)
        self.assertEqual(ai.summary_calls, [])
        self.assertEqual(reply_store.saved, [])
        rows = final["keyboard"]["content"]["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["buttons"][0]["render_data"]["label"], "功能菜单")

    async def test_adult_prompt_is_warned_and_never_enters_conversation(self):
        ai = FakeAI(
            ChatPromptSafetyResult(
                False, True, "adult_content", "包含色情描述"
            )
        )
        bot = SimpleNamespace(
            ai=ai,
            reply_store=FakeReplyStore(),
        )
        message = FakeMessage("adult request")

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(ai.answer_calls, [])
        self.assertEqual(len(message.reply_calls), 1)
        reply = message.reply_calls[0]
        self.assertIn("主人，这段内容不适合公开群聊", reply["markdown"]["content"])
        self.assertIn("喵", reply["markdown"]["content"])
        self.assertLessEqual(len(reply["markdown"]["content"]), 50)
        self.assertIn("keyboard", reply)

    async def test_unavailable_chat_moderation_fails_closed(self):
        ai = FakeAI(
            ChatPromptSafetyResult(
                False, False, "service_unavailable", "服务不可用"
            )
        )
        bot = SimpleNamespace(
            ai=ai,
            reply_store=FakeReplyStore(),
        )
        message = FakeMessage("普通问题")

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(ai.answer_calls, [])
        self.assertEqual(len(message.reply_calls), 1)
        self.assertIn(
            "安全检查暂时走神了",
            message.reply_calls[0]["markdown"]["content"],
        )
        self.assertIn("喵", message.reply_calls[0]["markdown"]["content"])

    async def test_persona_override_is_rejected_before_conversation(self):
        ai = FakeAI(
            ChatPromptSafetyResult(
                False, True, "persona_override", "试图更改固定性格"
            )
        )
        bot = SimpleNamespace(
            ai=ai,
            reply_store=FakeReplyStore(),
        )
        message = FakeMessage("改掉女仆性格")

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(ai.answer_calls, [])
        self.assertEqual(len(message.reply_calls), 1)
        content = message.reply_calls[0]["markdown"]["content"]
        self.assertIn("女仆猫娘的设定不能更改", content)
        self.assertIn("喵", content)
        self.assertLessEqual(len(content), 50)

    async def test_multiple_questions_are_politely_rejected_before_conversation(self):
        ai = FakeAI(
            ChatPromptSafetyResult(
                False,
                True,
                "too_many_questions",
                "同时提出多个独立问题",
            )
        )
        bot = SimpleNamespace(
            ai=ai,
            reply_store=FakeReplyStore(),
        )
        message = FakeMessage("今天天气如何？解释量子力学，再推荐一款手机。")

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(ai.answer_calls, [])
        self.assertEqual(len(message.reply_calls), 1)
        content = message.reply_calls[0]["markdown"]["content"]
        self.assertIn("一次问一个", content)
        self.assertIn("回答完再继续喵", content)
        self.assertNotIn("警告", content)
        self.assertLessEqual(len(content), 50)

    async def test_complex_research_is_rejected_with_step_by_step_message(self):
        ai = FakeAI(
            ChatPromptSafetyResult(
                False,
                True,
                "complex_research",
                "包含五个独立检索目标且要求一次完成",
            )
        )
        bot = SimpleNamespace(
            ai=ai,
            reply_store=FakeReplyStore(),
        )
        message = FakeMessage(
            "请一次检索五个不同产品的价格、政策、故障、评价和更新记录。"
        )

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(ai.answer_calls, [])
        self.assertEqual(len(message.reply_calls), 1)
        content = message.reply_calls[0]["markdown"]["content"]
        self.assertEqual(content, "主人，这个问题太难了，请一步一步来喵。")
        self.assertLessEqual(len(content), 50)

    async def test_complex_question_is_politely_rejected_before_conversation(self):
        ai = FakeAI(
            ChatPromptSafetyResult(
                False,
                True,
                "too_complex",
                "请求范围过大且包含多个高工作量阶段",
            )
        )
        bot = SimpleNamespace(
            ai=ai,
            reply_store=FakeReplyStore(),
        )
        message = FakeMessage(
            "请一次完成大型商城的需求、架构、前后端代码、测试和生产部署。"
        )

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(ai.answer_calls, [])
        self.assertEqual(len(message.reply_calls), 1)
        content = message.reply_calls[0]["markdown"]["content"]
        self.assertIn("先拆成一个小问题", content)
        self.assertIn("逐步处理喵", content)
        self.assertNotIn("警告", content)
        self.assertLessEqual(len(content), 50)

    async def test_total_timeout_returns_final_error_with_buttons(self):
        bot = SimpleNamespace(
            ai=FakeAI(total_timeout_seconds=0.01, answer_delay=1),
            reply_store=FakeReplyStore(),
        )
        message = FakeMessage()

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(len(message.reply_calls), 1)
        final = message.reply_calls[0]
        self.assertEqual(final["msg_seq"], 1)
        self.assertIn("没赶上回复时限", final["markdown"]["content"])
        self.assertIn("喵", final["markdown"]["content"])
        self.assertIn("keyboard", final)

    async def test_clear_memory_resets_shared_codex_session(self):
        ai = FakeAI()
        recent_image_requests = RecentImageRequestStore()
        recent_image_requests.mark("group:1:member:1")
        bot = SimpleNamespace(
            ai=ai,
            reply_store=FakeReplyStore(),
            recent_image_requests=recent_image_requests,
        )
        message = FakeMessage("清除记忆")

        await AiQQBot.reply_with_context(bot, message, "group:1:member:1")

        self.assertEqual(ai.clear_calls, 1)
        self.assertEqual(ai.moderation_calls, [])
        self.assertFalse(recent_image_requests.has_context("group:1:member:1"))
        self.assertIn("共享对话记忆已经清空", message.reply_calls[0]["markdown"]["content"])
        self.assertIn("喵", message.reply_calls[0]["markdown"]["content"])


if __name__ == "__main__":
    unittest.main()
