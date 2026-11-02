"""Application composition root."""

from __future__ import annotations

import os
from dataclasses import dataclass

from aiqq.config import AppConfig
from aiqq.database import (
    GroupMessageRepository,
    ImageUsageRepository,
    NovelAIPromptSessionRepository,
    NovelAIStyleRepository,
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
from aiqq.logic.conversation import ConversationWorkflow, INITIAL_CHAT_HISTORY_LIMIT
from aiqq.logic.gpt_usage import GPTUsageAccount, GPTUsageWorkflow
from aiqq.logic.image_audit import ImageAuditPolicy
from aiqq.logic.image_quota import ImageQuotaManager
from aiqq.logic.image_generation import GPTImageWorkflow
from aiqq.logic.novelai import (
    NovelAIImageWorkflow,
    NovelAIPromptWorkflow,
    NovelAIQuickImageWorkflow,
)
from aiqq.logic.novelai_style import NovelAIStyleWorkflow
from aiqq.services.agents import (
    ChatAgent,
    ImagePromptAuditAgent,
    NovelAIPromptAgent,
    PromptAuditAgent,
)
from aiqq.services.ai import CodexSDKBackend, ResponsesBackend
from aiqq.services.chat_read import GroupChatReadService
from aiqq.services.gpt_usage import GPTUsageClient
from aiqq.services.images import (
    GPTImageService,
    GroupReferenceImageLoader,
    MCPClient,
    NaiV5Service,
    NovelAIService,
    WebImageService,
)
from aiqq.services.novelai_tags import NaiV5TagSearchService, NovelAITagSearchService
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
    novelai_styles = NovelAIStyleRepository(state_connection)
    nai_v5_styles = NovelAIStyleRepository(state_connection, provider="nai_v5")
    image_usage = ImageQuotaManager(
        image_usage_repository,
        daily_limit=config.images.user_daily_limit,
        nai_v5_daily_limit=config.images.nai_v5_daily_limit,
        nai_v5_whitelist=config.images.nai_v5_whitelist,
    )
    backend = _build_ai_backend(config)
    prompt_auditor = PromptAuditAgent(backend)
    image_auditor = ImagePromptAuditAgent(backend)
    image_audit_policy = ImageAuditPolicy(config.images.image_audit_whitelist)
    gpt_images = GPTImageService(
        api_key=config.images.api_key,
        base_url=config.images.base_url,
        model=config.images.model,
        timeout_seconds=config.images.request_timeout_seconds,
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
    nai_v5_token = _load_secret(
        direct=config.images.nai_v5_mcp_token,
        file_path=config.images.nai_v5_mcp_token_file,
        name="NAI_V5_MCP_TOKEN_FILE",
    )
    nai_v5_client = (
        MCPClient(
            url=config.images.nai_v5_mcp_url,
            token=nai_v5_token,
            timeout_seconds=config.images.novelai_timeout_seconds,
        )
        if nai_v5_token else None
    )
    nai_v5_images = NaiV5Service(
        nai_v5_client, max_concurrent=config.images.novelai_max_concurrent
    )
    novelai_prompt_agent = NovelAIPromptAgent(
        backend, tag_search=NovelAITagSearchService(novelai_client)
    )
    nai_v5_prompt_agent = NovelAIPromptAgent(
        backend, tag_search=NaiV5TagSearchService(nai_v5_client)
    )
    web_images = WebImageService(
        timeout_seconds=config.images.web_download_timeout_seconds
    )
    reference_images = GroupReferenceImageLoader(
        repository=group_messages,
        downloader=web_images,
    )
    chat_agent = ChatAgent(
        backend,
        system_prompt=config.ai.system_prompt,
        web_search_enabled=config.ai.web_search_enabled,
        gpt_image_skill_enabled=(
            config.ai.backend == "codex_sdk" and config.ai.gpt_image_skill_enabled
        ),
        chat_read_service=GroupChatReadService(repository=group_messages, downloader=web_images),
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
        history_message_limit=INITIAL_CHAT_HISTORY_LIMIT,
    )
    gpt_image_workflow = GPTImageWorkflow(
        prompt_auditor=image_auditor,
        image_service=gpt_images,
        image_usage=image_usage,
        audit_policy=image_audit_policy,
    )
    novelai_image_workflow = NovelAIImageWorkflow(
        prompt_auditor=image_auditor,
        image_service=novelai_images,
        image_usage=image_usage,
        styles=novelai_styles,
        audit_policy=image_audit_policy,
    )
    nai_v5_image_workflow = NovelAIImageWorkflow(
        prompt_auditor=image_auditor,
        image_service=nai_v5_images,
        image_usage=image_usage,
        styles=nai_v5_styles,
        provider_name="Nai v5",
        command_name="Nai v5 作图",
        provider_id="nai_v5",
        quota_provider="nai_v5",
        audit_policy=image_audit_policy,
    )
    novelai_style_workflow = NovelAIStyleWorkflow(styles=novelai_styles)
    nai_v5_style_workflow = NovelAIStyleWorkflow(
        styles=nai_v5_styles,
        provider_name="Nai v5",
        command_name="Nai v5 画风",
        provider_id="nai_v5",
    )
    novelai_prompt_workflow = NovelAIPromptWorkflow(
        prompt_auditor=image_auditor,
        prompt_agent=novelai_prompt_agent,
        sessions=prompt_sessions,
        audit_policy=image_audit_policy,
    )
    novelai_quick_image_workflow = NovelAIQuickImageWorkflow(
        prompt_workflow=novelai_prompt_workflow,
        image_workflow=novelai_image_workflow,
    )
    nai_v5_quick_image_workflow = NovelAIQuickImageWorkflow(
        prompt_workflow=NovelAIPromptWorkflow(
            prompt_auditor=image_auditor,
            prompt_agent=nai_v5_prompt_agent,
            sessions=prompt_sessions,
            audit_policy=image_audit_policy,
        ),
        image_workflow=nai_v5_image_workflow,
    )
    gpt_usage_client = GPTUsageClient(config.gpt_usage.base_url)
    gpt_usage_workflow = GPTUsageWorkflow(
        fetcher=gpt_usage_client,
        accounts=(
            GPTUsageAccount(account.member_openid, account.label, account.api_key)
            for account in config.gpt_usage.accounts
        ),
        admin_member_ids=config.gpt_usage.admin_member_ids,
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
            novelai_quick_image_workflow=novelai_quick_image_workflow,
            novelai_prompt_workflow=novelai_prompt_workflow,
            novelai_style_workflow=novelai_style_workflow,
            nai_v5_image_workflow=nai_v5_image_workflow,
            nai_v5_quick_image_workflow=nai_v5_quick_image_workflow,
            nai_v5_style_workflow=nai_v5_style_workflow,
            gpt_usage_workflow=gpt_usage_workflow,
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
            message_storage_snapshot=group_messages.storage_status,
            novelai_snapshot=lambda: {
                "configured": novelai_images.is_configured,
                "ready": novelai_images.is_ready,
            },
            nai_v5_snapshot=lambda: {
                "configured": nai_v5_images.is_configured,
                "ready": nai_v5_images.is_ready,
            },
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
                novelai_styles.initialize,
                nai_v5_styles.initialize,
                novelai_images.initialize,
                nai_v5_images.initialize,
                media_store.initialize,
                reply_store.initialize,
            ),
            closers=(
                web_images.close,
                novelai_images.close,
                nai_v5_images.close,
                gpt_images.close,
                gpt_usage_client.close,
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
        max_output_tokens=config.ai.max_output_tokens,
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
