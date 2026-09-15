"""Build the aiohttp application from injected handlers."""

from __future__ import annotations

from aiohttp import web

from .admin_messages import AdminMessageHandler, register_admin_message_routes
from .health import HealthHandler
from .group_messages import GroupMessageViewer, register_group_message_routes
from .media import MediaHandler, register_media_routes
from .replies import ReplyHandler, register_reply_routes


def create_web_application(
    *,
    health: HealthHandler,
    admin_messages: AdminMessageHandler,
    media: MediaHandler,
    replies: ReplyHandler,
    group_messages: GroupMessageViewer,
) -> web.Application:
    application = web.Application(client_max_size=32 * 1024)
    application.router.add_get("/health", health.serve)
    register_admin_message_routes(application, admin_messages)
    register_media_routes(application, media)
    register_reply_routes(application, replies)
    register_group_message_routes(application, group_messages)
    return application
