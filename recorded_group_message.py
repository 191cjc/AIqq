import logging
import mimetypes
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

from group_message_store import GroupMessageStore


logger = logging.getLogger(__name__)


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _json_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if depth >= 5:
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _json_value(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item, depth=depth + 1) for item in value]
    values = getattr(value, "__dict__", None)
    if isinstance(values, dict):
        return _json_value(values, depth=depth + 1)
    return str(value)


class RecordedGroupMessage:
    """Delegates a QQ group message and records successful bot replies."""

    def __init__(
        self,
        message: Any,
        store: GroupMessageStore,
        *,
        bot_username: str,
    ):
        self._message = message
        self._store = store
        self._bot_username = bot_username
        self._uploaded_media_urls: dict[str, str] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._message, name)

    def _record_uploaded_media(self, file_info: str, public_url: str) -> None:
        if file_info and public_url:
            self._uploaded_media_urls[file_info] = public_url

    async def reply(self, **kwargs):
        response = await self._message.reply(**kwargs)
        response_id = _field(response, "id")
        if not isinstance(response_id, str) or not response_id:
            logger.warning("QQ 群回复成功响应缺少消息 ID，无法写入群消息记录")
            return response

        timestamp = _field(response, "timestamp")
        if not isinstance(timestamp, str) or not timestamp:
            timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        message_type = kwargs.get("msg_type", 0)
        if isinstance(message_type, bool) or not isinstance(message_type, int):
            message_type = 0

        payload = self._payload(
            response_id=response_id,
            timestamp=timestamp,
            message_type=message_type,
            reply=kwargs,
        )
        content = self._content(kwargs, message_type=message_type)
        try:
            await self._store.save_bot_reply(
                message_id=response_id,
                group_openid=str(self.group_openid or ""),
                username=self._bot_username,
                content=content,
                message_type=message_type,
                sent_at=timestamp,
                source_message_id=str(self.id or ""),
                payload=payload,
            )
        except Exception:
            logger.exception("机器人群回复写入消息数据库失败")
        return response

    def _payload(
        self,
        *,
        response_id: str,
        timestamp: str,
        message_type: int,
        reply: dict[str, Any],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": response_id,
            "group_openid": str(self.group_openid or ""),
            "message_type": message_type,
            "timestamp": timestamp,
            "source_message_id": str(self.id or ""),
            "author": {
                "bot": True,
                "username": self._bot_username,
            },
        }
        for name in (
            "content",
            "markdown",
            "keyboard",
            "media",
            "embed",
            "ark",
            "message_reference",
            "msg_seq",
        ):
            if name in reply and reply[name] is not None:
                payload[name] = _json_value(reply[name])

        media = reply.get("media")
        file_info = _field(media, "file_info")
        public_url = (
            self._uploaded_media_urls.pop(file_info, "")
            if isinstance(file_info, str)
            else ""
        )
        if public_url:
            parsed = urlparse(public_url)
            filename = unquote(PurePosixPath(parsed.path).name) or "bot-image"
            content_type = mimetypes.guess_type(filename)[0] or "image/jpeg"
            payload["attachments"] = [
                {
                    "content_type": content_type,
                    "filename": filename,
                    "url": public_url,
                }
            ]
        return payload

    @staticmethod
    def _content(reply: dict[str, Any], *, message_type: int) -> str:
        content = reply.get("content")
        if isinstance(content, str) and content:
            return content
        markdown = reply.get("markdown")
        markdown_content = _field(markdown, "content")
        if isinstance(markdown_content, str) and markdown_content:
            return markdown_content
        return "[图片]" if message_type == 7 else "[机器人消息]"
