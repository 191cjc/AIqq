"""Application composition root."""

from __future__ import annotations

import os
from dataclasses import dataclass

from aiqq.config import AppConfig
from aiqq.database import (
    GroupMessageRepository,
    ImageUsageRepository,
    NovelAIPromptSessionRepository,
    SQLiteConnection,
)
from aiqq.interfaces.qq.client import AiQQClient, ClientComponents
from aiqq.interfaces.qq.command_router import ExplicitCommandRouter
from aiqq.interfaces.qq.handlers import GroupMessageHandler
from aiqq.interfaces.qq.reply import ConversationReplySender
from aiqq.interfaces.web import (
    AdminMessageHandler,
    GroupMessageViewer,
    HealthHandler,
    MediaHandler,
    ReplyHandler,
    create_web_application,
)
from aiqq.logic.conversation import ConversationWorkflow
from aiqq.logic.image_quota import ImageQuotaManager
from aiqq.logic.image_generation import GPTImageWorkflow
from aiqq.logic.novelai import NovelAIImageWorkflow, NovelAIPromptWorkflow
from aiqq.services.agents import (
    ChatAgent,
    ImagePromptAuditAgent,
    NovelAIPromptAgent,
    PromptAuditAgent,
)
from aiqq.services.ai import CodexSDKBackend, ResponsesBackend
from aiqq.services.images import (
    GroupReferenceImageLoader,
    MCPClient,
    NovelAIService,
    WebImageService,
)
from aiqq.services.images.codex_responses import CodexResponsesImageService
from aiqq.services.qq import PanelService, QQMessageSender
from aiqq.services.storage import TemporaryMediaStore, TemporaryReplyStore


@dataclass(frozen=True)
class Application:
    config: AppConfig
    client: AiQQClient

    def run(self) -> None:
        self.client.run(appid=self.config.qq.app_id, secret=self.config.qq.secret)


def build_application(config: AppConfig) -> Application:
    group_messages = GroupMessageRepository(
        SQLiteConnection(config.history.database_path)
    )
    state_connection = SQLiteConnection(config.storage.state_database_path)
    image_usage_repository = ImageUsageRepository(state_connection)
    prompt_sessions = NovelAIPromptSessionRepository(state_connection)
    image_usage = ImageQuotaManager(
        image_usage_repository,
        daily_limit=config.images.user_daily_limit,
    )
    backend = _build_ai_backend(config)
    prompt_auditor = PromptAuditAgent(backend)
    image_auditor = ImagePromptAuditAgent(backend)
    novelai_prompt_agent = NovelAIPromptAgent(backend)
    chat_agent = ChatAgent(
        backend,
        system_prompt=config.ai.system_prompt,
        web_search_enabled=config.ai.web_search_enabled,
        gpt_image_skill_enabled=(
            config.ai.backend == "codex_sdk" and config.ai.gpt_image_skill_enabled
        ),
    )
    gpt_images = CodexResponsesImageService(
        api_key=config.images.api_key,
        base_url=config.images.base_url,
        model=config.images.model,
        driver_model=config.images.driver_model,
        timeout_seconds=config.images.request_timeout_seconds,
        cli_path=config.ai.codex_cli,
        runtime_dir=config.ai.codex_runtime_dir,
        work_dir=config.ai.codex_work_dir,
        process_environment=os.environ,
    )
    novelai_token = _load_secret(
        direct=config.images.novelai_mcp_token,
        file_path=config.images.novelai_mcp_token_file,
        name="NOVELAI_MCP_TOKEN_FILE",
    )
    novelai_client = (
        MCPClient(
            url=config.images.novelai_mcp_url,
            token=novelai_token,
            timeout_seconds=config.images.novelai_timeout_seconds,
        )
        if config.images.novelai_mcp_url and novelai_token
        else None
    )
    novelai_images = NovelAIService(
        novelai_client,
        max_concurrent=config.images.novelai_max_concurrent,
    )
    web_images = WebImageService(
        timeout_seconds=config.images.web_download_timeout_seconds
    )
    reference_images = GroupReferenceImageLoader(
        repository=group_messages,
        downloader=web_images,
    )
    conversation = ConversationWorkflow(
        prompt_auditor=prompt_auditor,
        history_repository=group_messages,
        conversation_agent=chat_agent,
        image_usage=image_usage,
        image_service=gpt_images,
        image_prompt_auditor=image_auditor,
        reference_image_loader=reference_images,
        web_image_service=(
            web_images if config.ai.web_image_search_enabled else None
        ),
        history_message_limit=config.history.max_messages,
        history_char_limit=config.history.max_chars,
    )
    gpt_image_workflow = GPTImageWorkflow(
        prompt_auditor=image_auditor,
        image_service=gpt_images,
        image_usage=image_usage,
    )
    novelai_image_workflow = NovelAIImageWorkflow(
        prompt_auditor=image_auditor,
        image_service=novelai_images,
        image_usage=image_usage,
    )
    novelai_prompt_workflow = NovelAIPromptWorkflow(
        prompt_auditor=image_auditor,
        prompt_agent=novelai_prompt_agent,
        sessions=prompt_sessions,
    )
    media_store = TemporaryMediaStore(
        config.storage.media_dir,
        config.storage.public_base_url,
        config.storage.media_ttl_seconds,
    )
    reply_store = TemporaryReplyStore(
        config.storage.reply_dir,
        config.storage.public_base_url,
        config.storage.reply_ttl_seconds,
    )
    admin_token = _load_secret(
        direct=config.web.admin_token,
        file_path=config.web.admin_token_file,
        name="AIQQ_ADMIN_TOKEN_FILE",
    )

    def create_components(client: AiQQClient) -> ClientComponents:
        sender = QQMessageSender(client.api, group_messages, bot_username="AiQQ")
        reply_sender = ConversationReplySender(
            sender=sender,
            reply_store=reply_store,
            media_store=media_store,
        )
        command_router = ExplicitCommandRouter(
            reply_sender=reply_sender,
            gpt_image_workflow=gpt_image_workflow,
            novelai_image_workflow=novelai_image_workflow,
            novelai_prompt_workflow=novelai_prompt_workflow,
        )
        message_handler = GroupMessageHandler(
            workflow=conversation,
            sender=sender,
            reply_sender=reply_sender,
            command_handler=command_router,
            defer_after_seconds=(
                min(
                    config.ai.request_timeout_seconds,
                    config.ai.total_timeout_seconds,
                )
                if config.ai.backend == "codex_sdk"
                else config.ai.total_timeout_seconds
            ),
        )
        health = HealthHandler(
            gateway_snapshot=client.gateway_status.snapshot,
            database_is_open=lambda: group_messages.is_open,
            ai_backend=config.ai.backend,
        )
        web_application = create_web_application(
            health=health,
            admin_messages=AdminMessageHandler(
                group_messages, sender, token=admin_token
            ),
            media=MediaHandler(media_store),
            replies=ReplyHandler(reply_store),
            group_messages=GroupMessageViewer(
                group_messages,
                sender,
                username=config.web.message_view_username,
                password=config.web.message_view_password,
            ),
        )
        return ClientComponents(
            group_messages=group_messages,
            message_handler=message_handler,
            sender=sender,
            panel=PanelService(client.http),
            web_application=web_application,
            initializers=(
                group_messages.initialize,
                image_usage_repository.initialize,
                prompt_sessions.initialize,
                novelai_images.initialize,
                media_store.initialize,
                reply_store.initialize,
            ),
            closers=(
                web_images.close,
                novelai_images.close,
                gpt_images.close,
                backend.close,
                group_messages.close,
                state_connection.close,
            ),
        )

    client = AiQQClient(
        qq_config=config.qq,
        web_config=config.web,
        component_factory=create_components,
    )
    return Application(config=config, client=client)


def _build_ai_backend(config: AppConfig):
    if config.ai.backend == "responses":
        return ResponsesBackend(
            api_key=config.ai.api_key,
            base_url=config.ai.base_url,
            model=config.ai.model,
            timeout_seconds=config.ai.request_timeout_seconds,
            max_output_tokens=config.ai.max_output_tokens,
            max_concurrent=config.ai.max_concurrent,
        )
    return CodexSDKBackend(
        api_key=config.ai.api_key,
        base_url=config.ai.base_url,
        model=config.ai.model,
        cli_path=config.ai.codex_cli,
        runtime_dir=config.ai.codex_runtime_dir,
        work_dir=config.ai.codex_work_dir,
        turn_timeout_seconds=config.ai.request_timeout_seconds,
        reasoning_effort=config.ai.reasoning_effort,
        utility_reasoning_effort=config.ai.utility_reasoning_effort,
        max_concurrent=config.ai.max_concurrent,
        process_environment=os.environ,
    )


def _load_secret(*, direct: str, file_path, name: str) -> str:
    if direct.strip():
        return direct.strip()
    if file_path is None:
        return ""
    try:
        if file_path.stat().st_mode & 0o077:
            raise ValueError(f"{name} permissions must be 0600")
        value = file_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"cannot read {name}") from exc
    if not value:
        raise ValueError(f"{name} must not be empty")
    return value
