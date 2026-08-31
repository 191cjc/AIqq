import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from aiohttp import web

from ai_service import env_int
from novelai_service import GeneratedImage


MEDIA_FILE_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}\.(?:png|jpg|webp)$")
MIME_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}


@dataclass(frozen=True)
class MediaAsset:
    file_name: str
    public_url: str


class TemporaryMediaStore:
    def __init__(self, directory: str, public_base_url: str, ttl_seconds: int):
        self.directory = Path(directory)
        self.public_base_url = public_base_url.rstrip("/")
        self.ttl_seconds = ttl_seconds

    @classmethod
    def from_env(cls) -> "TemporaryMediaStore":
        return cls(
            os.getenv("AIQQ_MEDIA_DIR", "/var/lib/aiqq/media").strip(),
            os.getenv(
                "AIQQ_PUBLIC_BASE_URL", "https://www.firesoul.cn/aiqq"
            ).strip(),
            env_int("AIQQ_MEDIA_TTL_SECONDS", 900, 60, 3600),
        )

    async def initialize(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.cleanup_expired()

    def save(self, image: GeneratedImage) -> MediaAsset:
        extension = MIME_EXTENSIONS.get(image.mime_type)
        if extension is None:
            raise ValueError("不支持的临时图片格式。")
        self.cleanup_expired()
        file_name = f"{secrets.token_urlsafe(32)}.{extension}"
        path = self.directory / file_name
        path.write_bytes(image.data)
        os.chmod(path, 0o600)
        return MediaAsset(
            file_name,
            f"{self.public_base_url}/media/{quote(file_name)}",
        )

    async def serve(self, request: web.Request) -> web.StreamResponse:
        file_name = request.match_info.get("file_name", "")
        if not MEDIA_FILE_RE.fullmatch(file_name):
            raise web.HTTPNotFound()
        path = self.directory / file_name
        try:
            age = time.time() - path.stat().st_mtime
        except OSError as exc:
            raise web.HTTPNotFound() from exc
        if age > self.ttl_seconds:
            path.unlink(missing_ok=True)
            raise web.HTTPNotFound()
        response = web.FileResponse(path)
        response.headers["Cache-Control"] = "private, max-age=60"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def cleanup_expired(self) -> None:
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
