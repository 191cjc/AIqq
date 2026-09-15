"""Private, expiring storage for images that QQ fetches over HTTPS."""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from aiqq.logic.models import ImageAsset


MEDIA_FILE_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}\.(?:png|jpg|webp)$")
MIME_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}


@dataclass(frozen=True)
class MediaExport:
    file_name: str
    public_url: str


class TemporaryMediaStore:
    def __init__(self, directory: Path, public_base_url: str, ttl_seconds: int) -> None:
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")
        self.directory = directory.expanduser()
        self.public_base_url = public_base_url.rstrip("/")
        self.ttl_seconds = ttl_seconds

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def save(self, image: ImageAsset) -> MediaExport:
        return await asyncio.to_thread(self._save_sync, image)

    async def resolve(self, file_name: str) -> Path | None:
        return await asyncio.to_thread(self._resolve_sync, file_name)

    async def cleanup_expired(self) -> None:
        await asyncio.to_thread(self._cleanup_expired_sync)

    def _initialize_sync(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self._cleanup_expired_sync()

    def _save_sync(self, image: ImageAsset) -> MediaExport:
        extension = MIME_EXTENSIONS.get(image.mime_type)
        if extension is None:
            raise ValueError("unsupported temporary image format")
        self._cleanup_expired_sync()
        file_name = f"{secrets.token_urlsafe(32)}.{extension}"
        path = self.directory / file_name
        with path.open("xb") as output:
            output.write(image.data)
        os.chmod(path, 0o600)
        return MediaExport(
            file_name=file_name,
            public_url=f"{self.public_base_url}/media/{quote(file_name)}",
        )

    def _resolve_sync(self, file_name: str) -> Path | None:
        if not MEDIA_FILE_RE.fullmatch(file_name):
            return None
        path = self.directory / file_name
        try:
            if not path.is_file() or time.time() - path.stat().st_mtime > self.ttl_seconds:
                path.unlink(missing_ok=True)
                return None
        except OSError:
            return None
        return path

    def _cleanup_expired_sync(self) -> None:
        if not self.directory.exists():
            return
        cutoff = time.time() - self.ttl_seconds
        for path in self.directory.iterdir():
            if not path.is_file() or not MEDIA_FILE_RE.fullmatch(path.name):
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue
