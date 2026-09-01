import asyncio
import os

import botpy
from aiohttp import web
from botpy import logging
from botpy.message import C2CMessage, GroupMessage
from dotenv import load_dotenv

from ai_service import (
    AIResult,
    AIService,
    SAFE_IMAGE_PROMPT_FALLBACK,
    clean_prompt,
    env_int,
    normalize_reply_summary,
)
from commands import (
    MEMORY_CLEAR_COMMANDS,
    NOVELAI_IMAGE_COMMAND,
    NOVELAI_PROMPT_COMMAND,
    NOVELAI_PROMPT_EDIT_COMMAND,
    extract_menu_suggestion,
    extract_novelai_prompt,
    extract_novelai_prompt_edit_request,
    extract_novelai_prompt_request,
)
from conversation_memory import ConversationManager, ConversationMemory
from image_generation import ImageUsageStore, MAX_IMAGE_PROMPT_CHARS
from media_store import TemporaryMediaStore
from message_ui import (
    reply_feature_menu,
    reply_research_stage,
    reply_with_novelai_prompt_options,
    reply_with_quick_menu,
)
from novelai_service import NovelAIError, NovelAIService
from panel_service import PanelService
from prompt_sessions import PromptSessionStore
from qq_media_service import QQMediaError, upload_qq_image
from reply_store import TemporaryReplyStore


load_dotenv()
logger = logging.get_logger()
MAX_RESEARCH_STAGE_REPLIES = 2
REPLY_SUMMARY_TIMEOUT_SECONDS = env_int(
    "AIQQ_REPLY_SUMMARY_TIMEOUT_SECONDS", 30, 5, 60
)


def format_novelai_prompt_options(prompts: tuple[str, ...]) -> str:
    sections = ["可选择以下 NovelAI 提示词方案："]
    sections.extend(
        f"方案{index}：\n{prompt}" for index, prompt in enumerate(prompts, 1)
    )
    return "\n\n".join(sections)


async def prepare_text_reply(bot, content: str) -> tuple[str, str | None]:
    full_reply_url = None
    reply_store = getattr(bot, "reply_store", None)
    if reply_store is not None:
        try:
            export = await asyncio.to_thread(reply_store.save, content)
            full_reply_url = export.public_url
        except Exception as error:
            logger.exception("保存完整回复失败：%s", error)

    try:
        async with asyncio.timeout(REPLY_SUMMARY_TIMEOUT_SECONDS):
            display_text = normalize_reply_summary(
                await bot.ai.summarize_reply(content)
            )
    except Exception as error:
        logger.warning("生成回复摘要失败，使用本地摘要：%s", error)
        display_text = normalize_reply_summary(content)
    return display_text, full_reply_url


async def send_text_reply(bot, message, content: str, **kwargs):
    display_text, full_reply_url = await prepare_text_reply(bot, content)
    return await reply_with_quick_menu(
        message,
        display_text,
        full_reply_url=full_reply_url,
        **kwargs,
    )


async def send_feature_menu_reply(bot, message, **kwargs):
    content = "请选择要使用的功能。"
    display_text, full_reply_url = await prepare_text_reply(bot, content)
    return await reply_feature_menu(
        message,
        content=display_text,
        full_reply_url=full_reply_url,
        **kwargs,
    )


async def send_novelai_prompt_options_reply(
    bot,
    message,
    content: str,
    prompts: tuple[str, ...],
    token: str,
    **kwargs,
):
    display_text, full_reply_url = await prepare_text_reply(bot, content)
    return await reply_with_novelai_prompt_options(
        message,
        display_text,
        prompts,
        token,
        full_reply_url=full_reply_url,
        **kwargs,
    )


async def reply_image_prompt_rejection(
    bot,
    message,
    reason: str,
    suggestion: str,
    *,
    original_prompt: str | None = None,
) -> None:
    safe_reason = (reason or "提示词未通过生成前检查。").replace("`", "'")
    safe_suggestion = (
        suggestion or SAFE_IMAGE_PROMPT_FALLBACK
    ).replace("`", "'")
    await send_text_reply(
        bot,
        message,
        "请求未生成。\n\n"
        f"拦截原因：{safe_reason}\n\n"
        f"建议英文提示词：`{safe_suggestion}`",
        novelai_suggestion=(
            safe_suggestion if original_prompt is None else None
        ),
        novelai_prompt_request=original_prompt,
    )


class AiQQBot(botpy.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ai = AIService.from_env()
        self.memory = ConversationMemory.from_env()
        self.conversations = ConversationManager(self.ai, self.memory)
        self.novelai = NovelAIService.from_env()
        self.image_usage = ImageUsageStore.from_env()
        self.prompt_sessions = PromptSessionStore.from_env()
        self.media_store = TemporaryMediaStore.from_env()
        self.reply_store = TemporaryReplyStore.from_env()
        self.panels = PanelService(self.http)
        self._panel_sync_lock = asyncio.Lock()
        self._panel_synced = False

    async def on_ready(self):
        await self.memory.initialize()
        await self.image_usage.initialize()
        await self.prompt_sessions.initialize()
        await self.media_store.initialize()
        await self.reply_store.initialize()
        logger.info("AiQQ 已登录：%s", self.robot.name)
        if self.ai.is_configured:
            logger.info("AI 服务已配置，模型：%s", self.ai.model)
        else:
            logger.warning("AI 服务尚未配置 OPENAI_API_KEY")
        await self.start_health_server()
        if self.novelai.is_configured:
            if await self.novelai.check_connection():
                logger.info("NovelAI MCP 已连接，仅启用文生图")
            else:
                logger.warning("NovelAI MCP 当前不可用")
        else:
            logger.warning("NovelAI MCP 尚未配置")
        await self.sync_command_panel()

    async def sync_command_panel(self):
        if self._panel_synced:
            return

        async with self._panel_sync_lock:
            if self._panel_synced:
                return
            try:
                result = await self.panels.sync_clear_memory_panel()
            except Exception as error:
                logger.exception("同步群聊指令面板失败：%s", error)
                return

            self._panel_synced = True
            logger.info(
                "群聊指令面板同步完成：action=%s panel_id=%s",
                result.action,
                result.panel_id or "unknown",
            )

    async def start_health_server(self):
        if getattr(self, "_health_runner", None) is not None:
            return

        app = web.Application()
        app.router.add_get("/health", self.health)
        app.router.add_get("/media/{file_name}", self.media_store.serve)
        app.router.add_get("/reply/{file_name}", self.reply_store.serve)
        runner = web.AppRunner(app)
        await runner.setup()

        host = os.getenv("AIQQ_HEALTH_HOST", "127.0.0.1")
        port = int(os.getenv("AIQQ_HEALTH_PORT", "8787"))
        await web.TCPSite(runner, host=host, port=port).start()
        self._health_runner = runner
        logger.info("健康检查已启动：http://%s:%s/health", host, port)

    async def health(self, _request: web.Request):
        return web.json_response(
            {
                "status": "ok",
                "service": "AiQQ",
                "ai_configured": self.ai.is_configured,
                "web_search_enabled": self.ai.web_search_enabled,
                "memory": "sqlite",
                "summary_every_rounds": self.memory.summary_every_rounds,
                "novelai_configured": self.novelai.is_configured,
                "novelai_ready": self.novelai.is_ready,
            }
        )

    async def close(self):
        runner = getattr(self, "_health_runner", None)
        if runner is not None:
            await runner.cleanup()
            self._health_runner = None
        await self.ai.close()
        await self.novelai.close()
        await super().close()

    async def on_group_at_message_create(self, message: GroupMessage):
        """响应 QQ 群中的 @ 消息。"""
        conversation_key = (
            f"group:{message.group_openid}:member:{message.author.member_openid}"
        )
        await self.reply_with_context(message, conversation_key)

    async def on_c2c_message_create(self, message: C2CMessage):
        """响应发送给机器人的单聊消息。"""
        conversation_key = f"c2c:{message.author.user_openid}"
        await self.reply_with_context(message, conversation_key)

    async def reply_with_context(self, message, conversation_key: str):
        prompt = clean_prompt(message.content)
        image_prompt = extract_novelai_prompt(prompt)
        if image_prompt is not None:
            await self.generate_novelai_image(
                message, conversation_key, image_prompt
            )
            return

        prompt_description = extract_novelai_prompt_request(prompt)
        if prompt_description is not None:
            await self.prepare_novelai_prompt(
                message, conversation_key, prompt_description
            )
            return

        prompt_edit = extract_novelai_prompt_edit_request(prompt)
        if prompt_edit is not None:
            token, request = prompt_edit
            await self.revise_novelai_prompt(
                message, conversation_key, token, request
            )
            return

        menu_suggestion = extract_menu_suggestion(prompt)
        if menu_suggestion is not None:
            prompt_request = (
                extract_novelai_prompt_request(menu_suggestion)
                if menu_suggestion
                else None
            )
            await send_feature_menu_reply(
                self,
                message,
                novelai_suggestion=(
                    menu_suggestion
                    if menu_suggestion and prompt_request is None
                    else None
                ),
                novelai_prompt_request=prompt_request,
            )
            return

        if prompt in MEMORY_CLEAR_COMMANDS:
            await self.conversations.clear(conversation_key)
            await send_text_reply(self, message, "已清除你的对话记忆。")
            return

        msg_seq = 1

        async def report_stage(content: str) -> None:
            nonlocal msg_seq
            if msg_seq > MAX_RESEARCH_STAGE_REPLIES:
                return
            current_seq = msg_seq
            msg_seq += 1
            await reply_research_stage(message, content, msg_seq=current_seq)

        try:
            async with asyncio.timeout(self.ai.total_timeout_seconds):
                moderation = await self.ai.moderate_chat_prompt(prompt)
                if not moderation.available:
                    await send_text_reply(
                        self,
                        message,
                        "内容安全检查暂时不可用，本次请求已拒绝，请稍后再试。",
                    )
                    return
                if not moderation.safe:
                    reason = moderation.reason.replace("`", "'")
                    if moderation.category == "adult_content":
                        logger.info("已拦截普通对话中的成人内容")
                        content = (
                            "警告：检测到不适合公开群聊的成人内容，本次请求已拒绝。"
                            f"\n\n原因：{reason}"
                        )
                    else:
                        logger.info("已拦截更改机器人固定角色的请求")
                        content = (
                            "角色设定固定，不能由用户更改，本次请求已拒绝。"
                            f"\n\n原因：{reason}"
                        )
                    await send_text_reply(self, message, content)
                    return
                result = await self.conversations.chat(
                    conversation_key,
                    prompt,
                    on_stage=report_stage,
                )
        except TimeoutError:
            logger.warning("AI 对话超过 %s 秒总时限", self.ai.total_timeout_seconds)
            result = AIResult("AI 查询时间过长，本次已停止，请稍后重试。", False)

        await send_text_reply(self, message, result.text, msg_seq=msg_seq)
        if result.success:
            await self.conversations.summarize_if_needed(conversation_key)

    async def prepare_novelai_prompt(
        self, message, conversation_key: str, description: str
    ) -> None:
        if not description:
            await send_text_reply(
                self,
                message,
                f"请在 `{NOVELAI_PROMPT_COMMAND}` 后输入画面描述，例如："
                f"`{NOVELAI_PROMPT_COMMAND} 一位站在樱花树下的白发少女`。",
            )
            return
        if len(description) > MAX_IMAGE_PROMPT_CHARS:
            await send_text_reply(
                self,
                message,
                f"画面描述超过了 {MAX_IMAGE_PROMPT_CHARS} 个字符的长度限制。",
            )
            return

        try:
            async with asyncio.timeout(self.ai.total_timeout_seconds):
                moderation = await self.ai.moderate_image_prompt(description)
                if not moderation.available or not moderation.safe:
                    await reply_image_prompt_rejection(
                        self,
                        message,
                        moderation.reason,
                        moderation.suggested_prompt,
                    )
                    return

                context = await self.conversations.load_context(conversation_key)
                result = await self.ai.create_novelai_prompts(
                    description,
                    history=context.model_history(),
                    summary=context.summary,
                )
                if not result.success:
                    await send_text_reply(self, message, result.error)
                    return

                output_moderation = await self.ai.moderate_image_prompt(
                    "\n".join(result.prompts)
                )
                if not output_moderation.available or not output_moderation.safe:
                    await reply_image_prompt_rejection(
                        self,
                        message,
                        output_moderation.reason,
                        output_moderation.suggested_prompt,
                    )
                    return
        except TimeoutError:
            await send_text_reply(
                self,
                message,
                "AI 提示词生成时间过长，本次已停止，请稍后重试。",
            )
            return

        session = await self.prompt_sessions.create(
            conversation_key, result.prompts
        )
        content = format_novelai_prompt_options(session.prompts)
        await self.conversations.record_round(
            conversation_key,
            f"{NOVELAI_PROMPT_COMMAND} {description}",
            content,
        )
        await self.conversations.summarize_if_needed(conversation_key)
        await send_novelai_prompt_options_reply(
            self,
            message,
            content,
            session.prompts,
            session.token,
            max_chars=self.ai.max_reply_chars,
        )

    async def revise_novelai_prompt(
        self,
        message,
        conversation_key: str,
        token: str,
        request: str,
    ) -> None:
        session = await self.prompt_sessions.load(conversation_key, token)
        if session is None:
            await send_text_reply(
                self,
                message,
                "提示词修改会话不存在或已超过 15 分钟，请重新使用 NovelAI提示词 指令。",
            )
            return
        if not request:
            await send_text_reply(
                self,
                message,
                "请在修改指令后继续输入要求，例如：方案2改成夜景，并使用半身构图。",
            )
            return
        if len(request) > MAX_IMAGE_PROMPT_CHARS:
            await send_text_reply(
                self,
                message,
                f"修改要求超过了 {MAX_IMAGE_PROMPT_CHARS} 个字符的长度限制。",
            )
            return

        try:
            async with asyncio.timeout(self.ai.total_timeout_seconds):
                moderation = await self.ai.moderate_image_prompt(request)
                if not moderation.available or not moderation.safe:
                    await reply_image_prompt_rejection(
                        self,
                        message,
                        moderation.reason,
                        moderation.suggested_prompt,
                    )
                    return

                context = await self.conversations.load_context(conversation_key)
                result = await self.ai.revise_novelai_prompts(
                    session.prompts,
                    request,
                    history=context.model_history(),
                    summary=context.summary,
                )
                if not result.success:
                    await send_text_reply(self, message, result.error)
                    return

                output_moderation = await self.ai.moderate_image_prompt(
                    "\n".join(result.prompts)
                )
                if not output_moderation.available or not output_moderation.safe:
                    await reply_image_prompt_rejection(
                        self,
                        message,
                        output_moderation.reason,
                        output_moderation.suggested_prompt,
                    )
                    return
        except TimeoutError:
            await send_text_reply(
                self,
                message,
                "AI 提示词修改时间过长，本次已停止，请稍后重试。",
            )
            return

        next_session = await self.prompt_sessions.create(
            conversation_key, result.prompts
        )
        content = format_novelai_prompt_options(next_session.prompts)
        await self.conversations.record_round(
            conversation_key,
            f"{NOVELAI_PROMPT_EDIT_COMMAND} {request}",
            content,
        )
        await self.conversations.summarize_if_needed(conversation_key)
        await send_novelai_prompt_options_reply(
            self,
            message,
            content,
            next_session.prompts,
            next_session.token,
            max_chars=self.ai.max_reply_chars,
        )

    async def generate_novelai_image(
        self, message, conversation_key: str, prompt: str
    ) -> None:
        if not prompt:
            await reply_image_prompt_rejection(
                self,
                message,
                f"没有在 `{NOVELAI_IMAGE_COMMAND}` 后提供图片提示词。",
                SAFE_IMAGE_PROMPT_FALLBACK,
                original_prompt=prompt,
            )
            return
        if len(prompt) > MAX_IMAGE_PROMPT_CHARS:
            await reply_image_prompt_rejection(
                self,
                message,
                f"图片提示词超过了 {MAX_IMAGE_PROMPT_CHARS} 个字符的长度限制。",
                SAFE_IMAGE_PROMPT_FALLBACK,
                original_prompt=prompt,
            )
            return
        if not self.novelai.is_configured:
            await send_text_reply(
                self,
                message,
                "NovelAI 生图服务尚未配置。",
            )
            return
        if not self.novelai.is_ready:
            await send_text_reply(
                self,
                message,
                "NovelAI MCP 尚未完成能力检查，生图功能暂不可用。",
            )
            return

        moderation = await self.ai.moderate_image_prompt(prompt)
        if not moderation.available:
            await reply_image_prompt_rejection(
                self,
                message,
                moderation.reason,
                moderation.suggested_prompt,
                original_prompt=prompt,
            )
            return
        if not moderation.safe:
            logger.info(
                "已拦截不安全的 NovelAI 提示词：category=%s",
                moderation.category,
            )
            await reply_image_prompt_rejection(
                self,
                message,
                moderation.reason,
                moderation.suggested_prompt,
                original_prompt=prompt,
            )
            return

        review = await self.ai.review_image_prompt(prompt)
        if not review.available:
            await reply_image_prompt_rejection(
                self,
                message,
                review.reason,
                review.suggested_prompt,
                original_prompt=prompt,
            )
            return
        if review.contains_chinese:
            await reply_image_prompt_rejection(
                self,
                message,
                review.reason,
                review.suggested_prompt,
                original_prompt=prompt,
            )
            return
        if not review.effective:
            await reply_image_prompt_rejection(
                self,
                message,
                review.reason,
                review.suggested_prompt,
                original_prompt=prompt,
            )
            return

        usage = await self.image_usage.reserve_attempt(conversation_key)
        if not usage.allowed:
            await send_text_reply(self, message, usage.message)
            return

        try:
            await send_text_reply(
                self,
                message,
                "提示词审核通过，NovelAI 正在生成图片...",
            )
            image = await self.novelai.generate(prompt)
            await self.image_usage.record_success(conversation_key)
            asset = self.media_store.save(image)
            media = await upload_qq_image(message, asset.public_url)
            try:
                await message.reply(
                    msg_type=7,
                    media=media,
                    msg_seq=2,
                )
            except Exception as exc:
                raise QQMediaError("发送 QQ 富媒体图片失败。") from exc
        except NovelAIError as exc:
            logger.warning("NovelAI 生图失败：%s", exc)
            await send_text_reply(
                self,
                message,
                "NovelAI 生图失败，请稍后重试。",
                msg_seq=2,
            )
        except QQMediaError as exc:
            logger.warning("QQ 图片上传失败：%s", exc)
            await send_text_reply(
                self,
                message,
                "图片已生成，但上传到 QQ 失败，请稍后重试。",
                msg_seq=2,
            )
        except Exception:
            logger.exception("NovelAI 生图流程发生未知错误")
            await send_text_reply(
                self,
                message,
                "NovelAI 生图服务暂时不可用。",
                msg_seq=2,
            )
        finally:
            await self.image_usage.finish_attempt(conversation_key)


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"缺少 {name}，请先在 .env 文件中填写。")
    return value


def qq_http_timeout_seconds() -> int:
    return env_int("AIQQ_QQ_HTTP_TIMEOUT_SECONDS", 30, 5, 120)


if __name__ == "__main__":
    intents = botpy.Intents(public_messages=True)
    client = AiQQBot(
        intents=intents,
        ext_handlers=False,
        timeout=qq_http_timeout_seconds(),
    )
    client.run(
        appid=required_env("QQ_BOT_APPID"),
        secret=required_env("QQ_BOT_SECRET"),
    )
