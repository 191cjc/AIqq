"""Loads an image referenced by an opaque group-message record ID."""

from __future__ import annotations

import logging

from aiqq.exceptions import ReferenceImageUnavailable
from aiqq.logic.models import ImageAsset
from aiqq.logic.ports import ReferenceImageURLRepository, WebImageService

from .web import WebImageError


logger = logging.getLogger(__name__)
_REFERENCE_ERROR_KINDS = {
    "not_found": "expired",
    "timeout": "timeout",
    "access_denied": "access_denied",
    "invalid_image": "invalid_image",
    "too_large": "too_large",
}


class GroupReferenceImageLoader:
    def __init__(
        self,
        *,
        repository: ReferenceImageURLRepository,
        downloader: WebImageService,
    ) -> None:
        self._repository = repository
        self._downloader = downloader

    async def load(self, group_id: str, record_id: int) -> ImageAsset:
        urls = await self._repository.list_image_urls(group_id, record_id)
        if not urls:
            raise ReferenceImageUnavailable(kind="missing")
        failed_kinds: set[str] = set()
        failed_statuses: set[int | None] = set()
        for url in urls:
            try:
                return await self._downloader.download(url)
            except WebImageError as exc:
                kind = _REFERENCE_ERROR_KINDS.get(exc.kind, "download_failed")
                status_code = exc.status_code
            except Exception:
                kind = "download_failed"
                status_code = None
            failed_kinds.add(kind)
            failed_statuses.add(status_code)
            logger.info(
                "event=reference_image_candidate_failed stage=reference_download "
                "record_id=%s error_kind=%s status=%s",
                record_id,
                kind,
                status_code,
            )
        # A single missing candidate cannot establish that every reference expired.
        kind = failed_kinds.pop() if len(failed_kinds) == 1 else "download_failed"
        status_code = failed_statuses.pop() if len(failed_statuses) == 1 else None
        raise ReferenceImageUnavailable(kind=kind, status_code=status_code)
