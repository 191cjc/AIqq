"""HTTP adapters."""

from .admin_messages import AdminMessageHandler, register_admin_message_routes
from .app import create_web_application
from .health import HealthHandler
from .group_messages import GroupMessageViewer, register_group_message_routes
from .media import MediaHandler, register_media_routes
from .replies import ReplyHandler, register_reply_routes

__all__ = [
    "AdminMessageHandler",
    "HealthHandler",
    "GroupMessageViewer",
    "MediaHandler",
    "ReplyHandler",
    "register_admin_message_routes",
    "register_media_routes",
    "register_group_message_routes",
    "register_reply_routes",
    "create_web_application",
]
