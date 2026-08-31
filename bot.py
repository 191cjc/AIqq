import asyncio
import os

import botpy
from aiohttp import web
from botpy import logging
from botpy.message import C2CMessage, GroupMessage
from dotenv import load_dotenv

from ai_service import AIResult, AIService, clean_prompt, env_int
from commands import MEMORY_CLEAR_COMMANDS, NOVELAI_IMAGE_COMMAND, extract_novelai_prompt
from conversation_memory import ConversationManager, ConversationMemory
from image_generation import ImageUsageStore, MAX_IMAGE_PROMPT_CHARS
from media_store import TemporaryMediaStore
from message_ui import reply_research_stage, reply_with_clear_memory_button
from novelai_service import NovelAIError, NovelAIService
from panel_service import PanelService
from qq_media_service import QQMediaError, upload_qq_image


load_dotenv()
logger = logging.get_logger()


class AiQQBot(botpy.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ai = AIService.from_env()
        self.memory = ConversationMemory.from_env()
        self.conversations = ConversationManager(self.ai, self.memory)
        self.novelai = NovelAIService.from_env()
        self.image_usage = ImageUsageStore.from_env()
        self.media_store = TemporaryMediaStore.from_env()
        self.panels = PanelService(self.http)
        self._panel_sync_lock = asyncio.Lock()
        self._panel_synced = False

    async def on_ready(self):
        await self.memory.initialize()
        await self.image_usage.initialize()
        await self.media_store.initialize()
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

        if prompt in MEMORY_CLEAR_COMMANDS:
            await self.conversations.clear(conversation_key)
            await reply_with_clear_memory_button(message, "已清除你的对话记忆。")
            return

        msg_seq = 1

        async def report_stage(content: str) -> None:
            nonlocal msg_seq
            current_seq = msg_seq
            msg_seq += 1
            await reply_research_stage(message, content, msg_seq=current_seq)

        try:
            async with asyncio.timeout(self.ai.total_timeout_seconds):
                result = await self.conversations.chat(
                    conversation_key,
                    prompt,
                    on_stage=report_stage,
                )
        except TimeoutError:
            logger.warning("AI 对话超过 %s 秒总时限", self.ai.total_timeout_seconds)
            result = AIResult("AI 查询时间过长，本次已停止，请稍后重试。", False)

        await reply_with_clear_memory_button(message, result.text, msg_seq=msg_seq)
        if result.success:
            await self.conversations.summarize_if_needed(conversation_key)

    async def generate_novelai_image(
        self, message, conversation_key: str, prompt: str
    ) -> None:
        if not prompt:
            await reply_with_clear_memory_button(
                message,
                f"请在 `{NOVELAI_IMAGE_COMMAND}` 后输入图片提示词。",
            )
            return
        if len(prompt) > MAX_IMAGE_PROMPT_CHARS:
            await reply_with_clear_memory_button(
                message,
                f"图片提示词不能超过 {MAX_IMAGE_PROMPT_CHARS} 个字符。",
            )
            return
        if not self.novelai.is_configured:
            await reply_with_clear_memory_button(
                message, "NovelAI 生图服务尚未配置。"
            )
            return
        if not self.novelai.is_ready:
            await reply_with_clear_memory_button(
                message,
                "NovelAI MCP 尚未完成能力检查，生图功能暂不可用。",
            )
            return

        moderation = await self.ai.moderate_image_prompt(prompt)
        if not moderation.available:
            await reply_with_clear_memory_button(
                message, "安全审核暂时不可用，本次未生成图片，请稍后重试。"
            )
            return
        if not moderation.safe:
            logger.info(
                "已拦截不安全的 NovelAI 提示词：category=%s",
                moderation.category,
            )
            await reply_with_clear_memory_button(
                message,
                "请求未生成：提示词包含不适合公开群聊的成人、裸露或性暗示内容，请修改后重试。",
            )
            return

        review = await self.ai.review_image_prompt(prompt)
        if not review.available:
            await reply_with_clear_memory_button(
                message,
                "提示词有效性检查暂时不可用，本次未生成图片，请稍后重试。",
            )
            return
        if review.contains_chinese:
            suggestion = review.suggested_prompt.replace("`", "'")
            await reply_with_clear_memory_button(
                message,
                "检测到提示词包含中文字符，建议改用英文后重新提交。\n\n"
                f"建议英文提示词：`{suggestion}`",
                novelai_suggestion=suggestion,
            )
            return
        if not review.effective:
            content = "请求未生成：提示词缺少明确、可生成的画面内容。"
            suggestion = ""
            if review.suggested_prompt:
                suggestion = review.suggested_prompt.replace("`", "'")
                content += f"\n\n修改建议：`{suggestion}`"
            await reply_with_clear_memory_button(
                message,
                content,
                novelai_suggestion=suggestion or None,
            )
            return

        usage = await self.image_usage.reserve_attempt(conversation_key)
        if not usage.allowed:
            await reply_with_clear_memory_button(message, usage.message)
            return

        try:
            await reply_with_clear_memory_button(
                message, "提示词审核通过，NovelAI 正在生成图片..."
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
            await reply_with_clear_memory_button(
                message, "NovelAI 生图失败，请稍后重试。", msg_seq=2
            )
        except QQMediaError as exc:
            logger.warning("QQ 图片上传失败：%s", exc)
            await reply_with_clear_memory_button(
                message,
                "图片已生成，但上传到 QQ 失败，请稍后重试。",
                msg_seq=2,
            )
        except Exception:
            logger.exception("NovelAI 生图流程发生未知错误")
            await reply_with_clear_memory_button(
                message, "NovelAI 生图服务暂时不可用。", msg_seq=2
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
