"""HTTP page for an expiring complete Markdown reply."""

from __future__ import annotations

import html

import mistune
from aiohttp import web

from aiqq.services.storage.temporary_reply import TemporaryReplyStore


class ReplyMarkdownRenderer(mistune.HTMLRenderer):
    def block_code(
        self, code: str, info: str | None = None, *, prompt: bool = False,
    ) -> str:
        tokens = (info or "").strip().split(maxsplit=1)
        language = html.escape(tokens[0] if tokens else "代码")
        language_class = f' class="language-{language}"' if tokens else ""
        kind = ' data-copy-kind="prompt"' if prompt else ""
        copy_label = "复制提示词" if prompt else "复制代码"
        return (
            '<div class="code-block">\n'
            '<div class="code-toolbar">'
            f'<span class="code-language">{language}</span>'
            f'<button class="copy-code" type="button" data-copy-code{kind} aria-label="{copy_label}">'
            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" '
            'stroke="currentColor" stroke-width="1.7" aria-hidden="true" focusable="false">'
            '<rect x="8" y="8" width="12" height="13" rx="2"/>'
            '<path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>'
            '</svg><span data-copy-label>复制</span></button></div>\n'
            f'<pre tabindex="0"><code{language_class}>{html.escape(code)}</code></pre></div>\n'
        )


MARKDOWN_RENDERER = mistune.create_markdown(
    renderer=ReplyMarkdownRenderer(escape=True),
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
            text=render_reply_page(
                document.content, pending=document.pending,
                prompt=request.query.get("view") == "prompt" and not document.pending,
            ),
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


REPLY_STYLES = """
    :root {
      color-scheme: light dark;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      --page: #f4f6f8; --surface: #fff; --ink: #253042; --muted: #738092;
      --border: #e5e9ef; --accent: #187467; --soft: #f1f4f7;
      --code-bg: #182230; --code-header: #202d3e; --code-ink: #e3eaf3;
    }
    * { box-sizing: border-box; }
    body { margin: 0; padding: 36px 20px; background: var(--page); color: var(--ink); }
    .reader {
      max-width: 900px; margin: 0 auto; padding: 36px 48px 48px;
      background: var(--surface); border: 1px solid var(--border); border-radius: 18px;
      box-shadow: 0 8px 40px #1b2b4010;
    }
    .page-header { padding-bottom: 26px; margin-bottom: 30px; border-bottom: 1px solid var(--border); }
    .brand { display: flex; align-items: center; gap: 9px; font-size: 13px; font-weight: 650; }
    .brand-mark {
      display: grid; place-items: center; width: 26px; height: 26px;
      border-radius: 8px; background: var(--accent); color: #fff; font-size: 13px;
    }
    .title-row { display: flex; align-items: center; gap: 14px; margin-top: 24px; }
    .page-header h1 { margin: 0; font-size: 28px; line-height: 1.35; letter-spacing: -.03em; }
    .status-tag { padding: 4px 9px; border-radius: 20px; background: var(--soft); color: var(--muted); font-size: 11px; }
    .status-tag.pending { color: #a36c15; }
    .subtitle { margin: 10px 0 0; color: var(--muted); font-size: 13px; line-height: 1.7; }
    article { min-width: 0; overflow-wrap: anywhere; font-size: 16px; line-height: 1.85; }
    article > :first-child { margin-top: 0; }
    article > :last-child { margin-bottom: 0; }
    article h1, article h2, article h3, article h4 { line-height: 1.45; margin: 1.7em 0 .65em; letter-spacing: -.02em; }
    article h1 { font-size: 26px; }
    article h2 { font-size: 22px; }
    article h3 { font-size: 18px; }
    article h4 { font-size: 16px; }
    p { margin: 1em 0; }
    a { color: var(--accent); text-underline-offset: 3px; text-decoration-thickness: 1px; }
    a:hover { text-decoration-thickness: 2px; }
    ul, ol { padding-left: 1.5em; margin: 1em 0; }
    li + li { margin-top: .35em; }
    li > p { margin: .5em 0; }
    code { font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace; }
    :not(pre) > code { padding: 3px 6px; border-radius: 5px; background: var(--soft); font-size: .86em; }
    .code-block { margin: 24px 0; overflow: hidden; border-radius: 11px; background: var(--code-bg); color: var(--code-ink); }
    .code-toolbar {
      display: flex; align-items: center; justify-content: space-between; gap: 12px;
      min-height: 44px; padding: 7px 14px; background: var(--code-header); border-bottom: 1px solid #ffffff0c;
    }
    .code-language { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #b4c1d2; font: 12px ui-monospace, monospace; }
    .copy-code {
      display: inline-flex; align-items: center; justify-content: center; gap: 6px; flex-shrink: 0;
      min-height: 30px; padding: 5px 10px; border: 1px solid #ffffff25; border-radius: 6px;
      background: transparent; color: #d4deeb; font: inherit; font-size: 12px; line-height: 1.5; cursor: pointer;
    }
    .copy-code:hover { background: #ffffff0d; border-color: #ffffff45; }
    .copy-code:disabled { cursor: wait; opacity: .7; }
    .copy-code.is-copied { color: #8fe3c5; border-color: #8fe3c560; }
    :focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
    .code-block pre:focus-visible { outline: 2px solid #8fe3c5; outline-offset: -3px; }
    pre { margin: 0; padding: 18px 20px; max-width: 100%; overflow-x: auto; overscroll-behavior-x: contain; tab-size: 4; line-height: 1.65; }
    pre code { padding: 0; border: 0; background: none; color: inherit; font-size: 13px; white-space: pre; }
    blockquote { margin: 24px 0; padding: 10px 18px; border-left: 3px solid var(--accent); border-radius: 0 8px 8px 0; background: var(--soft); color: var(--muted); }
    blockquote p { margin: .4em 0; }
    table { display: block; max-width: 100%; overflow-x: auto; margin: 24px 0; border-collapse: collapse; font-size: 14px; }
    th, td { padding: 10px 14px; border: 1px solid var(--border); text-align: left; }
    th { background: var(--soft); font-weight: 600; }
    hr { margin: 30px 0; border: 0; border-top: 1px solid var(--border); }
    img { max-width: 100%; height: auto; }
    .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
    .clipboard-buffer { position: fixed; left: -10000px; top: 0; font-size: 16px; }
    @media (prefers-color-scheme: dark) {
      :root { --page: #101722; --surface: #17202c; --ink: #dce4ef; --muted: #9aa8bb; --border: #2c3747; --accent: #83d7c1; --soft: #222d3d; }
      .brand-mark { color: #122a23; }
      .status-tag.pending { color: #e7bf7a; }
      .reader { box-shadow: none; }
    }
    @media (max-width: 600px) {
      body { padding: 0; background: var(--surface); }
      .reader { padding: 26px 18px 36px; border: 0; border-radius: 0; box-shadow: none; }
      .page-header { padding-bottom: 22px; margin-bottom: 25px; }
      .page-header h1 { font-size: 25px; }
      .title-row { margin-top: 21px; }
      pre { padding: 15px; }
      .code-toolbar { padding: 7px 12px; }
      th, td { padding: 8px 10px; }
    }
"""

REPLY_SCRIPT = """
    const status = document.getElementById('copy-status');

    async function copyCode(code) {
      const text = code.textContent;
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch (_) {}

      const previousFocus = document.activeElement;
      const buffer = document.createElement('textarea');
      buffer.className = 'clipboard-buffer';
      buffer.value = text;
      buffer.readOnly = true;
      document.body.appendChild(buffer);
      let copied = false;
      try {
        buffer.focus();
        buffer.select();
        buffer.setSelectionRange(0, text.length);
        copied = document.execCommand('copy');
      } catch (_) {}
      finally {
        buffer.remove();
        previousFocus.focus({ preventScroll: true });
      }
      if (!copied) {
        const range = document.createRange();
        range.selectNodeContents(code);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
      }
      return copied;
    }

    document.querySelectorAll('[data-copy-code]').forEach((button, index) => {
      button.addEventListener('click', async () => {
        const code = button.closest('.code-block').querySelector('pre > code');
        const label = button.querySelector('[data-copy-label]');
        button.disabled = true;
        const copied = await copyCode(code);
        button.disabled = false;
        button.focus({ preventScroll: true });
        label.textContent = copied ? '已复制' : '已选中';
        button.classList.toggle('is-copied', copied);
        const subject = button.dataset.copyKind === 'prompt' ? '提示词' : `第 ${index + 1} 段代码`;
        status.textContent = copied
          ? `${subject}已复制。`
          : `已选中${subject}，请使用系统复制操作。`;
        window.setTimeout(() => {
          label.textContent = '复制';
          button.classList.remove('is-copied');
        }, 2000);
      });
    });
"""


def render_reply_page(
    content: str, *, pending: bool = False, prompt: bool = False,
) -> str:
    prompt = prompt and not pending
    rendered = (
        ReplyMarkdownRenderer(escape=True).block_code(content, "提示词", prompt=True)
        if prompt else MARKDOWN_RENDERER(content)
    )
    refresh = '  <meta http-equiv="refresh" content="3">\n' if pending else ""
    heading = "任务进展" if pending else "完整输出"
    subtitle = "内容会自动更新，完成后可在此查看完整回复。" if pending else "代码块可单独复制。"
    if prompt:
        heading = "本次提示词"
        subtitle = "点击复制即可复用本次提示词。"
    status_tag = '<span class="status-tag pending">处理中</span>' if pending else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
{refresh}  <title>AiQQ {heading}</title>
  <style>
{REPLY_STYLES}
  </style>
</head>
<body>
  <main class="reader">
    <header class="page-header">
      <div class="brand"><span class="brand-mark" aria-hidden="true">A</span>AiQQ</div>
      <div class="title-row"><h1>{heading}</h1>{status_tag}</div>
      <p class="subtitle">{subtitle}</p>
    </header>
    <article>{rendered}</article>
  </main>
  <div id="copy-status" class="sr-only" role="status" aria-live="polite"></div>
  <script>
{REPLY_SCRIPT}
  </script>
</body>
</html>"""
