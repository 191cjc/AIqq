"""HTTP page for an expiring complete Markdown reply."""

from __future__ import annotations

import html

import mistune
from aiohttp import web

from aiqq.services.storage.temporary_reply import TemporaryReplyStore


MARKDOWN_RENDERER = mistune.create_markdown(
    escape=True,
    plugins=("strikethrough", "table", "task_lists", "url"),
)


class ReplyHandler:
    def __init__(self, store: TemporaryReplyStore) -> None:
        self._store = store

    async def serve(self, request: web.Request) -> web.Response:
        document = await self._store.load_document(
            request.match_info.get("file_name", "")
        )
        if document is None:
            raise web.HTTPNotFound()
        response = web.Response(
            text=render_reply_page(document.content, pending=document.pending),
            content_type="text/html",
            charset="utf-8",
        )
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response


def register_reply_routes(application: web.Application, handler: ReplyHandler) -> None:
    application.router.add_get("/reply/{file_name}", handler.serve)


def render_reply_page(content: str, *, pending: bool = False) -> str:
    rendered = MARKDOWN_RENDERER(content)
    raw = html.escape(content)
    refresh = '  <meta http-equiv="refresh" content="3">\n' if pending else ""
    heading = "任务进展" if pending else "完整输出"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
{refresh}  <title>AiQQ {heading}</title>
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
    body {{ max-width: 880px; margin: 0 auto; padding: 24px; line-height: 1.7; }}
    header {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; }}
    article {{ overflow-wrap: anywhere; }}
    pre {{ overflow-x: auto; padding: 12px; background: CanvasText; color: Canvas; }}
    table {{ display: block; overflow-x: auto; border-collapse: collapse; }}
    th, td {{ padding: 6px 10px; border: 1px solid GrayText; }}
    button {{ min-height: 40px; padding: 0 14px; }}
    #raw {{ position: fixed; width: 1px; height: 1px; opacity: 0; }}
    @media (max-width: 520px) {{ header {{ align-items: stretch; flex-direction: column; }} }}
  </style>
</head>
<body>
  <header><h1>{heading}</h1><button id="copy" type="button">复制完整原文</button></header>
  <article>{rendered}</article>
  <p id="status" role="status" aria-live="polite"></p>
  <textarea id="raw" tabindex="-1" aria-hidden="true">{raw}</textarea>
  <script>
    const raw = document.getElementById('raw');
    const status = document.getElementById('status');
    document.getElementById('copy').addEventListener('click', async () => {{
      try {{ await navigator.clipboard.writeText(raw.value); status.textContent = '已复制'; }}
      catch (_) {{ raw.focus(); raw.select(); status.textContent = '已选中，请使用系统复制操作'; }}
    }});
  </script>
</body>
</html>"""
