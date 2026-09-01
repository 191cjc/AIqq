import html
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import mistune
from aiohttp import web

from ai_service import env_int


REPLY_FILE_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}\.txt$")
MARKDOWN_RENDERER = mistune.create_markdown(
    escape=True,
    plugins=("strikethrough", "table", "task_lists", "url"),
)


@dataclass(frozen=True)
class ReplyExport:
    file_name: str
    public_url: str


class TemporaryReplyStore:
    def __init__(self, directory: str, public_base_url: str, ttl_seconds: int):
        self.directory = Path(directory)
        self.public_base_url = public_base_url.rstrip("/")
        self.ttl_seconds = ttl_seconds

    @classmethod
    def from_env(cls) -> "TemporaryReplyStore":
        return cls(
            os.getenv("AIQQ_REPLY_DIR", "/var/lib/aiqq/replies").strip(),
            os.getenv(
                "AIQQ_PUBLIC_BASE_URL", "https://www.firesoul.cn/aiqq"
            ).strip(),
            env_int("AIQQ_REPLY_TTL_SECONDS", 3600, 300, 86400),
        )

    async def initialize(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.cleanup_expired()

    def save(self, content: str) -> ReplyExport:
        self.cleanup_expired()
        file_name = f"{secrets.token_urlsafe(32)}.txt"
        path = self.directory / file_name
        path.write_text(content, encoding="utf-8")
        os.chmod(path, 0o600)
        return ReplyExport(
            file_name,
            f"{self.public_base_url}/reply/{quote(file_name)}",
        )

    async def serve(self, request: web.Request) -> web.Response:
        file_name = request.match_info.get("file_name", "")
        if not REPLY_FILE_RE.fullmatch(file_name):
            raise web.HTTPNotFound()
        path = self.directory / file_name
        try:
            age = time.time() - path.stat().st_mtime
            if age > self.ttl_seconds:
                path.unlink(missing_ok=True)
                raise web.HTTPNotFound()
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise web.HTTPNotFound() from exc

        response = web.Response(
            text=_reply_page(content),
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

    def cleanup_expired(self) -> None:
        if not self.directory.exists():
            return
        cutoff = time.time() - self.ttl_seconds
        for path in self.directory.iterdir():
            if not path.is_file() or not REPLY_FILE_RE.fullmatch(path.name):
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue


def _reply_page(content: str) -> str:
    escaped_content = html.escape(content)
    rendered_content = MARKDOWN_RENDERER(content)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AiQQ 完整原文</title>
  <style>
    :root {{
      color-scheme: light dark;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --page: #f5f7f9;
      --surface: #ffffff;
      --text: #20262e;
      --muted: #65717e;
      --border: #d9e0e7;
      --accent: #0b6bcb;
      --code: #eef2f5;
      --quote: #e5eef8;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--page); color: var(--text); line-height: 1.7; }}
    header {{
      position: sticky;
      top: 0;
      z-index: 1;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      min-height: 64px;
      padding: 10px max(20px, calc((100vw - 880px) / 2));
      border-bottom: 1px solid var(--border);
      background: var(--surface);
    }}
    .brand {{ font-size: 18px; font-weight: 700; letter-spacing: 0; }}
    button {{
      min-height: 40px;
      padding: 0 16px;
      border: 1px solid var(--accent);
      border-radius: 6px;
      background: var(--accent);
      color: #ffffff;
      font: inherit;
      font-weight: 600;
      cursor: pointer;
    }}
    button:focus-visible {{ outline: 3px solid var(--quote); outline-offset: 2px; }}
    main {{ max-width: 880px; margin: 0 auto; padding: 28px 20px 48px; }}
    article {{ overflow-wrap: anywhere; }}
    article > :first-child {{ margin-top: 0; }}
    article > :last-child {{ margin-bottom: 0; }}
    h1, h2, h3, h4 {{ margin: 1.5em 0 0.55em; line-height: 1.3; letter-spacing: 0; }}
    h1 {{ font-size: 26px; }}
    h2 {{ padding-bottom: 0.3em; border-bottom: 1px solid var(--border); font-size: 22px; }}
    h3 {{ font-size: 18px; }}
    p, ul, ol, blockquote, pre, table {{ margin: 0 0 1em; }}
    ul, ol {{ padding-left: 1.6em; }}
    li + li {{ margin-top: 0.25em; }}
    a {{ color: var(--accent); text-underline-offset: 3px; }}
    blockquote {{
      padding: 8px 16px;
      border-left: 4px solid var(--accent);
      background: var(--quote);
      color: var(--muted);
    }}
    blockquote > :last-child {{ margin-bottom: 0; }}
    code {{
      padding: 0.14em 0.36em;
      border-radius: 4px;
      background: var(--code);
      font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
      font-size: 0.92em;
    }}
    pre {{ overflow-x: auto; padding: 16px; border: 1px solid var(--border); background: var(--code); }}
    pre code {{ padding: 0; background: transparent; }}
    table {{ display: block; width: 100%; overflow-x: auto; border-collapse: collapse; }}
    th, td {{ padding: 8px 12px; border: 1px solid var(--border); text-align: left; }}
    th {{ background: var(--code); }}
    hr {{ margin: 2em 0; border: 0; border-top: 1px solid var(--border); }}
    input[type="checkbox"] {{ margin-right: 0.5em; }}
    #status {{ min-height: 24px; margin: 20px 0 0; color: var(--muted); }}
    .raw-source {{ position: fixed; width: 1px; height: 1px; opacity: 0; pointer-events: none; }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --page: #15191e;
        --surface: #1d232a;
        --text: #e8edf2;
        --muted: #aab4bf;
        --border: #39434d;
        --accent: #58a6e7;
        --code: #242c34;
        --quote: #202f3d;
      }}
      button {{ color: #10202e; }}
    }}
    @media (max-width: 520px) {{
      header {{ align-items: stretch; flex-direction: column; padding: 12px 16px; }}
      button {{ width: 100%; }}
      main {{ padding: 22px 16px 40px; }}
    }}
  </style>
</head>
<body>
  <header><div class="brand">完整输出</div><button id="copy" type="button">复制完整原文</button></header>
  <main>
    <article id="content">{rendered_content}</article>
    <p id="status" role="status" aria-live="polite"></p>
  </main>
  <textarea id="raw" class="raw-source" aria-hidden="true" tabindex="-1">{escaped_content}</textarea>
  <script>
    const button = document.getElementById('copy');
    const raw = document.getElementById('raw');
    const status = document.getElementById('status');
    button.addEventListener('click', async () => {{
      try {{
        await navigator.clipboard.writeText(raw.value);
        status.textContent = '已复制完整原文';
      }} catch (error) {{
        raw.focus();
        raw.select();
        status.textContent = '已选中完整原文，请使用系统复制操作';
      }}
    }});
  </script>
</body>
</html>"""
