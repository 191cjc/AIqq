"""Private, expiring storage for complete long-form replies."""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


REPLY_FILE_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}\.txt$")
PENDING_FILE_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}\.txt\.pending$")


@dataclass(frozen=True)
class ReplyExport:
    file_name: str
    public_url: str


@dataclass(frozen=True)
class ReplyDocument:
    content: str
    pending: bool


class TemporaryReplyStore:
    def __init__(self, directory: Path, public_base_url: str, ttl_seconds: int) -> None:
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")
        self.directory = directory.expanduser()
        self.public_base_url = public_base_url.rstrip("/")
        self.ttl_seconds = ttl_seconds

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def save(self, content: str) -> ReplyExport:
        if not content.strip():
            raise ValueError("reply content must not be empty")
        return await asyncio.to_thread(self._save_sync, content)

    async def create_pending(self, content: str) -> ReplyExport:
        if not content.strip():
            raise ValueError("reply content must not be empty")
        return await asyncio.to_thread(self._create_pending_sync, content)

    async def update(self, file_name: str, content: str, *, pending: bool) -> bool:
        if not content.strip():
            raise ValueError("reply content must not be empty")
        return await asyncio.to_thread(
            self._update_sync, file_name, content, pending=pending
        )

    async def load(self, file_name: str) -> str | None:
        document = await self.load_document(file_name)
        return None if document is None else document.content

    async def load_document(self, file_name: str) -> ReplyDocument | None:
        return await asyncio.to_thread(self._load_document_sync, file_name)

    async def cleanup_expired(self) -> None:
        await asyncio.to_thread(self._cleanup_expired_sync)

    def _initialize_sync(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self._cleanup_expired_sync()

    def _save_sync(self, content: str) -> ReplyExport:
        self._cleanup_expired_sync()
        file_name = f"{secrets.token_urlsafe(32)}.txt"
        path = self.directory / file_name
        with path.open("x", encoding="utf-8") as output:
            output.write(content)
        os.chmod(path, 0o600)
        return ReplyExport(
            file_name=file_name,
            public_url=f"{self.public_base_url}/reply/{quote(file_name)}",
        )

    def _create_pending_sync(self, content: str) -> ReplyExport:
        exported = self._save_sync(content)
        path = self.directory / exported.file_name
        marker = self._pending_path(path)
        try:
            marker.touch(mode=0o600, exist_ok=False)
            os.chmod(marker, 0o600)
        except OSError:
            path.unlink(missing_ok=True)
            marker.unlink(missing_ok=True)
            raise
        return exported

    def _update_sync(
        self, file_name: str, content: str, *, pending: bool
    ) -> bool:
        if not REPLY_FILE_RE.fullmatch(file_name):
            return False
        path = self.directory / file_name
        marker = self._pending_path(path)
        temporary_path: Path | None = None
        try:
            if time.time() - path.stat().st_mtime > self.ttl_seconds:
                self._unlink_reply(path)
                return False
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.directory,
                prefix=".reply-",
                delete=False,
            ) as output:
                temporary_path = Path(output.name)
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, path)
            temporary_path = None
            if pending:
                marker.touch(mode=0o600, exist_ok=True)
                os.chmod(marker, 0o600)
            else:
                marker.unlink(missing_ok=True)
            return True
        except OSError:
            return False
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _load_document_sync(self, file_name: str) -> ReplyDocument | None:
        if not REPLY_FILE_RE.fullmatch(file_name):
            return None
        path = self.directory / file_name
        try:
            if time.time() - path.stat().st_mtime > self.ttl_seconds:
                self._unlink_reply(path)
                return None
            return ReplyDocument(
                content=path.read_text(encoding="utf-8"),
                pending=self._pending_path(path).is_file(),
            )
        except (OSError, UnicodeError):
            return None

    def _cleanup_expired_sync(self) -> None:
        if not self.directory.exists():
            return
        cutoff = time.time() - self.ttl_seconds
        for path in self.directory.iterdir():
            if not path.is_file() or not REPLY_FILE_RE.fullmatch(path.name):
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    self._unlink_reply(path)
            except OSError:
                continue
        for path in self.directory.iterdir():
            if not path.is_file() or not PENDING_FILE_RE.fullmatch(path.name):
                continue
            reply_path = path.with_name(path.name.removesuffix(".pending"))
            if not reply_path.is_file():
                path.unlink(missing_ok=True)

    @staticmethod
    def _pending_path(path: Path) -> Path:
        return path.with_name(f"{path.name}.pending")

    def _unlink_reply(self, path: Path) -> None:
        path.unlink(missing_ok=True)
        self._pending_path(path).unlink(missing_ok=True)
