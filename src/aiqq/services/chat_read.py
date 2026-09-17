"""Server-bound group reads with per-conversation resource budgets."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from aiqq.logic.models import ImageAsset
from aiqq.services.images.chat_decode import DecodedChatImage
from aiqq.services.images.web import WebImageError


logger = logging.getLogger(__name__)
MAX_IMAGE_SOURCES = 3
MAX_HISTORY_PAGES = 3
MAX_HISTORY_PAGE_SIZE = 50
IMAGE_READ_MESSAGES = {
    "expired": "这张图片链接已过期或失效，请重新发送图片。",
    "missing": "未找到这条消息的可用图片，请指定图片消息或重新发送图片。",
    "recalled": "这条消息已撤回，无法继续读取其中的图片。",
    "timeout": "读取图片超时，请稍后重试，或重新发送图片。",
    "access_denied": "无法访问这张图片，请重新发送图片。",
    "invalid_image": "这张图片无法解码，请重新发送有效的图片。",
    "too_large": "这张图片或动图过大，读取失败，请压缩后重新发送。",
    "download_failed": "图片下载失败，请稍后重试，或重新发送图片。",
    "budget_exhausted": "本次最多读取3张来源图片，请指定最需要查看的图片。",
    "invalid_request": "图片读取参数无效，请使用消息记录和附件编号。",
    "closed": "本次图片读取会话已经结束。",
    "unavailable": "图片读取服务暂不可用，请稍后重试。",
}
_HISTORY_KEYS = {
    "before_record_id", "record_id", "message_id", "sender", "keyword",
    "sent_after", "sent_before", "limit", "versions", "before_version_id",
    "events", "before_event_record_id",
}


class _Repository(Protocol):
    async def query_history(self, group_id: str, **filters: Any) -> dict[str, Any]: ...

    async def get_full_record(
        self, group_id: str, record_id: int, version_id: int | None = None,
    ) -> dict[str, Any] | None: ...

    async def get_image_attachment(
        self, group_id: str, record_id: int, attachment_index: int = 0,
        version_id: int | None = None,
    ) -> dict[str, Any] | None: ...

    async def record_image_read(self, group_id: str, record_id: int, **fields: Any) -> bool: ...


class _Downloader(Protocol):
    async def download_for_read(self, url: str) -> DecodedChatImage: ...


class GroupChatReadService:
    def __init__(self, *, repository: _Repository, downloader: _Downloader) -> None:
        self._repository = repository
        self._downloader = downloader

    def create_session(self, group_id: str) -> GroupChatReadSession:
        if not isinstance(group_id, str) or not group_id:
            raise ValueError("chat read requires a group")
        return GroupChatReadSession(
            group_id=group_id, repository=self._repository, downloader=self._downloader,
        )


class GroupChatReadSession:
    def __init__(self, *, group_id: str, repository: _Repository, downloader: _Downloader) -> None:
        self._group_id = group_id
        self._repository = repository
        self._downloader = downloader
        self._lock = asyncio.Lock()
        self._history_pages = 0
        self._history_ranges: list[dict[str, Any]] = []
        self._retrieved_record_ids: set[int] = set()
        self._image_record_ids: set[int] = set()
        self._image_tasks: dict[
            tuple[int, int, int | None],
            asyncio.Task[tuple[dict[str, Any], tuple[ImageAsset, ...]]],
        ] = {}
        self._closed = False

    @property
    def retrieved_record_ids(self) -> frozenset[int]:
        return frozenset(self._retrieved_record_ids)

    @property
    def image_record_ids(self) -> frozenset[int]:
        return frozenset(self._image_record_ids)

    @property
    def all_image_read_failures(self) -> tuple[dict[str, Any], ...]:
        failures = []
        for task in self._image_tasks.values():
            if not task.done() or task.cancelled() or task.exception() is not None:
                return ()
            metadata, images = task.result()
            if images or metadata.get("status") != "error":
                return ()
            failures.append(dict(metadata))
        return tuple(failures)

    async def read_history(self, **filters: Any) -> dict[str, Any]:
        if not _valid_history_filters(filters):
            return {"status": "error", "error_kind": "invalid_request",
                    "message": "历史查询参数无效，请使用同群消息编号、时间、发送者或关键词。"}
        async with self._lock:
            if self._closed:
                return {"status": "error", "error_kind": "closed", "message": "本次历史读取会话已经结束。"}
            if self._history_pages >= MAX_HISTORY_PAGES:
                return {"status": "error", "error_kind": "budget_exhausted",
                        "message": "本次已读取3页历史，请缩小查询范围后再问。",
                        "pages_read": self._history_pages, "ranges_read": list(self._history_ranges)}
            self._history_pages += 1
        try:
            page = await self._repository.query_history(self._group_id, **filters)
        except Exception as exc:
            logger.warning("event=chat_history_read_failed error_type=%s", type(exc).__name__)
            return {"status": "error", "error_kind": "unavailable", "message": "历史读取暂不可用，请稍后重试。"}
        if self._closed:
            return {"status": "error", "error_kind": "closed", "message": "本次历史读取会话已经结束。"}
        ids = [_record_id(message) for message in page.get("messages", [])]
        valid_ids = [record_id for record_id in ids if record_id is not None]
        self._retrieved_record_ids.update(valid_ids)
        for message in page.get("messages", []):
            record_id = _record_id(message)
            if record_id is not None and message.get("derived", {}).get("has_image") and not _recalled(message):
                self._image_record_ids.add(record_id)
        self._history_ranges.append({
            "first_record_id": min(valid_ids) if valid_ids else None,
            "last_record_id": max(valid_ids) if valid_ids else None,
            "count": len(ids), "has_more": page.get("has_more"),
        })
        return {**page, "status": "ok", "pages_read": self._history_pages,
                "pages_remaining": MAX_HISTORY_PAGES - self._history_pages,
                "records_complete": True}

    async def read_image(
        self, record_id: int, attachment_index: int = 0,
        version_id: int | None = None,
    ) -> tuple[dict[str, Any], tuple[ImageAsset, ...]]:
        if (not _positive_int(record_id) or type(attachment_index) is not int
                or attachment_index < 0 or (version_id is not None and not _positive_int(version_id))):
            return _image_error("invalid_request")
        key = (record_id, attachment_index, version_id)
        async with self._lock:
            if self._closed:
                return _image_error("closed")
            task = self._image_tasks.get(key)
            if task is None:
                if len(self._image_tasks) >= MAX_IMAGE_SOURCES:
                    return _image_error("budget_exhausted")
                task = asyncio.create_task(self._read_image(record_id, attachment_index, version_id))
                self._image_tasks[key] = task
        # A single request shares downloads, including failures; no cross-request cache.
        return await task

    async def _read_image(
        self, record_id: int, attachment_index: int, version_id: int | None,
    ) -> tuple[dict[str, Any], tuple[ImageAsset, ...]]:
        try:
            current = await self._repository.get_full_record(self._group_id, record_id)
            if current is None:
                return _image_error("missing")
            self._retrieved_record_ids.add(record_id)
            if _recalled(current):
                return _image_error("recalled")
            if current.get("derived", {}).get("has_image"):
                self._image_record_ids.add(record_id)
            attachment = await self._repository.get_image_attachment(
                self._group_id, record_id, attachment_index=attachment_index, version_id=version_id,
            )
            if not attachment or not attachment.get("url"):
                return _image_error("missing")
        except Exception as exc:
            logger.warning("event=chat_image_lookup_failed error_type=%s", type(exc).__name__)
            return _image_error("unavailable")
        provenance = {
            "record_id": record_id, "attachment_index": attachment_index,
            "version_id": attachment.get("version_id", version_id),
            "attachment_id": attachment.get("attachment_id"),
            "source_path": attachment.get("path"),
        }
        try:
            decoded = await self._downloader.download_for_read(attachment["url"])
        except WebImageError as exc:
            kind = "expired" if exc.kind == "not_found" else exc.kind
            if kind not in IMAGE_READ_MESSAGES:
                kind = "download_failed"
            await self._record_read(record_id, attachment_index, attachment, "failed", kind,
                                    None)
            result, images = _image_error(kind)
            return {**result, **provenance, "http_status": exc.status_code}, images
        except Exception as exc:
            logger.warning("event=chat_image_download_failed error_type=%s", type(exc).__name__)
            await self._record_read(record_id, attachment_index, attachment, "failed", "download_failed", None)
            result, images = _image_error("download_failed")
            return {**result, **provenance}, images
        await self._record_read(record_id, attachment_index, attachment, "ok", "", decoded.metadata)
        return ({"status": "ok", **provenance, "metadata": decoded.metadata,
                 "image_count": len(decoded.images), "pixels_available": True,
                 "viewing_required": True}, decoded.images)

    async def _record_read(
        self, record_id: int, attachment_index: int, attachment: dict[str, Any],
        status: str, error_kind: str, metadata: dict[str, Any] | None,
    ) -> None:
        try:
            await self._repository.record_image_read(
                self._group_id, record_id,
                attachment_id=attachment.get("attachment_id"),
                version_id=attachment.get("version_id"), attachment_index=attachment_index,
                status=status, error_kind=error_kind, metadata=metadata,
            )
        except Exception as exc:
            # A metadata write failure must not pretend a successfully read image expired.
            logger.warning("event=chat_image_read_metadata_failed error_type=%s", type(exc).__name__)

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            tasks = list(self._image_tasks.values())
            self._image_tasks.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._retrieved_record_ids.clear()
        self._image_record_ids.clear()
        self._history_ranges.clear()


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _valid_history_filters(filters: dict[str, Any]) -> bool:
    if set(filters) - _HISTORY_KEYS:
        return False
    for key, value in filters.items():
        if key in {"before_record_id", "record_id", "before_version_id", "before_event_record_id"}:
            if value is not None and not _positive_int(value):
                return False
        elif key == "limit":
            if type(value) is not int or not 1 <= value <= MAX_HISTORY_PAGE_SIZE:
                return False
        elif key in {"versions", "events"}:
            if type(value) is not bool:
                return False
        elif not isinstance(value, str) or len(value) > 1000:
            return False
    return True


def _record_id(wire: Any) -> int | None:
    if not isinstance(wire, dict):
        return None
    value = wire.get("record", {}).get("record_id")
    return value if _positive_int(value) else None


def _recalled(wire: dict[str, Any]) -> bool:
    return bool(wire.get("record", {}).get("recalled_at")) or wire.get("derived", {}).get("recall_state") == "confirmed"


def _image_error(kind: str) -> tuple[dict[str, Any], tuple[ImageAsset, ...]]:
    return {"status": "error", "error_kind": kind, "message": IMAGE_READ_MESSAGES[kind],
            "pixels_available": False}, ()
