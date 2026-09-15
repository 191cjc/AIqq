"""Loads an image referenced by an opaque group-message record ID."""

from __future__ import annotations

import logging

from aiqq.logic.models import ImageAsset
from aiqq.logic.ports import ReferenceImageURLRepository, WebImageService


logger = logging.getLogger(__name__)


class GroupReferenceImageLoader:
    def __init__(
        self,
        *,
        repository: ReferenceImageURLRepository,
        downloader: WebImageService,
    ) -> None:
        self._repository = repository
        self._downloader = downloader

    async def load(self, group_id: str, record_id: int) -> ImageAsset | None:
        urls = await self._repository.list_image_urls(group_id, record_id)
        for url in urls:
            try:
                return await self._downloader.download(url)
            except Exception as exc:
                logger.info(
                    "event=reference_image_candidate_failed record_id=%s error_type=%s",
                    record_id,
                    type(exc).__name__,
                )
        return None
