"""Request-scoped, read-only history and vision capabilities."""

from __future__ import annotations

from typing import Any, Protocol

from .models import ImageAsset


class ChatReadSession(Protocol):
    @property
    def retrieved_record_ids(self) -> frozenset[int]: ...

    @property
    def image_record_ids(self) -> frozenset[int]: ...

    @property
    def all_image_read_failures(self) -> tuple[dict[str, Any], ...]:
        """Completed error results only when every attempted image read failed."""
        ...

    async def read_history(self, **filters: Any) -> dict[str, Any]: ...

    async def read_image(
        self, record_id: int, attachment_index: int = 0,
        version_id: int | None = None,
    ) -> tuple[dict[str, Any], tuple[ImageAsset, ...]]: ...

    async def close(self) -> None: ...


class ChatReadServiceProtocol(Protocol):
    def create_session(self, group_id: str) -> ChatReadSession: ...
