"""HTTP delivery for temporary QQ media."""

from __future__ import annotations

from aiohttp import web

from aiqq.services.storage.temporary_media import TemporaryMediaStore


class MediaHandler:
    def __init__(self, store: TemporaryMediaStore) -> None:
        self._store = store

    async def serve(self, request: web.Request) -> web.StreamResponse:
        path = await self._store.resolve(request.match_info.get("file_name", ""))
        if path is None:
            raise web.HTTPNotFound()
        response = web.FileResponse(path)
        response.headers["Cache-Control"] = "private, max-age=60"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response


def register_media_routes(application: web.Application, handler: MediaHandler) -> None:
    application.router.add_get("/media/{file_name}", handler.serve)
