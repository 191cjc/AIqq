import base64
import binascii
import hmac
import html
import os
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlencode, urlparse

from aiohttp import web

from group_message_store import (
    GroupMessageStore,
    GroupMessageSummary,
    StoredGroupMessage,
)


DEFAULT_VIEW_USERNAME = "admin"
MESSAGE_PAGE_SIZE = 50
MAX_ELEMENT_DEPTH = 4
MAX_VISIBLE_ELEMENTS = 50


class GroupMessageViewer:
    def __init__(
        self,
        store: GroupMessageStore,
        *,
        username: str,
        password: str,
    ):
        self.store = store
        self.username = username
        self.password = password

    @classmethod
    def from_env(cls, store: GroupMessageStore) -> "GroupMessageViewer":
        return cls(
            store,
            username=(
                os.getenv(
                    "AIQQ_GROUP_MESSAGE_VIEW_USERNAME", DEFAULT_VIEW_USERNAME
                ).strip()
                or DEFAULT_VIEW_USERNAME
            ),
            password=os.getenv(
                "AIQQ_GROUP_MESSAGE_VIEW_PASSWORD", ""
            ).strip(),
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.username and self.password)

    async def serve(self, request: web.Request) -> web.Response:
        if not self.is_configured:
            return self._response(
                "群消息查看页面尚未配置管理员口令。",
                status=503,
                content_type="text/plain",
            )
        if not self._is_authorized(request.headers.get("Authorization", "")):
            response = self._response(
                "需要管理员身份验证。",
                status=401,
                content_type="text/plain",
            )
            response.headers["WWW-Authenticate"] = (
                'Basic realm="AiQQ group messages", charset="UTF-8"'
            )
            return response

        groups = await self.store.groups()
        selected_group = request.rel_url.query.get("group", "")[:256]
        available_groups = {group.group_openid for group in groups}
        if selected_group not in available_groups:
            selected_group = groups[0].group_openid if groups else ""

        before_record_id = self._positive_int(
            request.rel_url.query.get("before")
        )
        messages = (
            await self.store.recent(
                selected_group,
                limit=MESSAGE_PAGE_SIZE,
                before_record_id=before_record_id,
            )
            if selected_group
            else ()
        )
        page = _render_page(groups, selected_group, messages)
        return self._response(page, content_type="text/html")

    def _is_authorized(self, authorization: str) -> bool:
        scheme, separator, encoded = authorization.partition(" ")
        if separator != " " or scheme.lower() != "basic" or not encoded:
            return False
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return False
        username, separator, password = decoded.partition(":")
        if separator != ":":
            return False
        username_matches = hmac.compare_digest(username, self.username)
        password_matches = hmac.compare_digest(password, self.password)
        return username_matches and password_matches

    @staticmethod
    def _positive_int(value: str | None) -> int | None:
        if value is None or not value.isdecimal():
            return None
        number = int(value)
        return number if number > 0 else None

    @staticmethod
    def _response(
        text: str,
        *,
        status: int = 200,
        content_type: str,
    ) -> web.Response:
        response = web.Response(
            text=text,
            status=status,
            content_type=content_type,
            charset="utf-8",
        )
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response


def _render_page(
    groups: tuple[GroupMessageSummary, ...],
    selected_group: str,
    messages: tuple[StoredGroupMessage, ...],
) -> str:
    selected_summary = next(
        (group for group in groups if group.group_openid == selected_group),
        None,
    )
    group_links = "".join(
        _render_group_link(group, group.group_openid == selected_group)
        for group in groups
    )
    if not group_links:
        group_links = (
            '<div class="empty compact">尚未收到群消息。请确认群管理员已开启'
            '“接收所有消息”。</div>'
        )

    message_items = "".join(
        _render_message(message) for message in reversed(messages)
    )
    if not message_items:
        message_items = '<div class="empty">该群暂无可显示的消息记录。</div>'

    group_title = (
        f"群组 {_short_id(selected_group)}" if selected_group else "群消息"
    )
    group_meta = ""
    if selected_summary is not None:
        group_meta = (
            f"{selected_summary.message_count} 条已记录消息 · "
            f"OpenID {_escape(selected_summary.group_openid)}"
        )

    older_link = ""
    if len(messages) == MESSAGE_PAGE_SIZE:
        oldest_id = min(message.record_id for message in messages)
        query = urlencode({"group": selected_group, "before": oldest_id})
        older_link = (
            f'<a class="older" href="?{_escape(query)}">查看更早的消息</a>'
        )

    refresh_query = urlencode({"group": selected_group}) if selected_group else ""
    refresh_url = f"?{_escape(refresh_query)}" if refresh_query else "?"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AiQQ 群消息</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --page: #f3f5f7;
      --surface: #ffffff;
      --surface-alt: #f8fafb;
      --text: #182026;
      --muted: #63707a;
      --border: #d9dfe4;
      --accent: #176b4d;
      --accent-soft: #e8f3ee;
      --selected: #edf5f1;
      --danger: #8d3b35;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--page); color: var(--text); letter-spacing: 0; }}
    a {{ color: inherit; }}
    header {{
      position: sticky;
      top: 0;
      z-index: 3;
      display: flex;
      align-items: center;
      justify-content: space-between;
      min-height: 58px;
      padding: 0 22px;
      border-bottom: 1px solid var(--border);
      background: var(--surface);
    }}
    .brand {{ font-size: 17px; font-weight: 700; }}
    .refresh {{
      min-width: 40px;
      min-height: 38px;
      display: inline-grid;
      place-items: center;
      border: 1px solid var(--border);
      border-radius: 6px;
      color: var(--accent);
      text-decoration: none;
      font-weight: 650;
    }}
    .layout {{
      display: grid;
      grid-template-columns: minmax(230px, 300px) minmax(0, 1fr);
      min-height: calc(100vh - 59px);
      max-width: 1500px;
      margin: 0 auto;
      background: var(--surface);
    }}
    aside {{ border-right: 1px solid var(--border); background: var(--surface-alt); }}
    .aside-title {{ padding: 18px 18px 10px; color: var(--muted); font-size: 13px; font-weight: 700; }}
    .group-link {{
      display: block;
      min-height: 78px;
      padding: 13px 18px;
      border-top: 1px solid transparent;
      border-bottom: 1px solid var(--border);
      text-decoration: none;
    }}
    .group-link:hover, .group-link.active {{ background: var(--selected); }}
    .group-link.active {{ box-shadow: inset 3px 0 var(--accent); }}
    .group-row {{ display: flex; justify-content: space-between; gap: 10px; align-items: baseline; }}
    .group-name {{ font-size: 14px; font-weight: 700; overflow-wrap: anywhere; }}
    .count {{ color: var(--accent); font-size: 12px; white-space: nowrap; }}
    .preview {{
      margin-top: 7px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    main {{ min-width: 0; }}
    .conversation-head {{
      position: sticky;
      top: 59px;
      z-index: 2;
      padding: 17px 24px;
      border-bottom: 1px solid var(--border);
      background: rgba(255,255,255,.96);
    }}
    h1 {{ margin: 0; font-size: 18px; line-height: 1.35; }}
    .meta {{ margin-top: 5px; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }}
    .messages {{ max-width: 920px; margin: 0 auto; padding: 22px 24px 48px; }}
    .message {{
      display: grid;
      grid-template-columns: 42px minmax(0, 1fr);
      gap: 12px;
      padding: 16px 0;
      border-bottom: 1px solid var(--border);
    }}
    .avatar {{
      width: 42px;
      height: 42px;
      display: grid;
      place-items: center;
      border-radius: 6px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 15px;
      font-weight: 800;
    }}
    .message-head {{ display: flex; flex-wrap: wrap; gap: 7px 10px; align-items: baseline; }}
    .author {{ font-size: 14px; font-weight: 750; }}
    .role, time {{ color: var(--muted); font-size: 11px; }}
    .body {{ margin-top: 7px; font-size: 14px; line-height: 1.65; overflow-wrap: anywhere; }}
    .body.muted {{ color: var(--muted); font-style: italic; }}
    .elements {{
      margin-top: 10px;
      padding: 10px 12px;
      border-left: 3px solid var(--accent);
      background: var(--surface-alt);
      color: #38434b;
      font-size: 13px;
      line-height: 1.55;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }}
    .attachments {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 12px; }}
    .attachment {{
      width: min(240px, 100%);
      border: 1px solid var(--border);
      border-radius: 6px;
      overflow: hidden;
      background: var(--surface-alt);
      text-decoration: none;
    }}
    .attachment img {{ display: block; width: 100%; max-height: 220px; object-fit: contain; background: #eef1f3; }}
    .attachment span {{ display: block; padding: 8px 10px; color: var(--muted); font-size: 11px; overflow-wrap: anywhere; }}
    .empty {{ padding: 54px 24px; color: var(--muted); text-align: center; line-height: 1.7; }}
    .empty.compact {{ padding: 20px 18px; font-size: 13px; text-align: left; }}
    .older {{
      display: block;
      width: fit-content;
      margin: 22px auto 0;
      padding: 10px 15px;
      border: 1px solid var(--accent);
      border-radius: 6px;
      color: var(--accent);
      text-decoration: none;
      font-size: 13px;
      font-weight: 700;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        color-scheme: dark;
        --page: #15191c;
        --surface: #1d2226;
        --surface-alt: #22282c;
        --text: #edf1f3;
        --muted: #a6b0b7;
        --border: #384147;
        --accent: #6ec39e;
        --accent-soft: #253c33;
        --selected: #273730;
      }}
      .conversation-head {{ background: rgba(29,34,38,.96); }}
      .elements {{ color: #d2d9dd; }}
      .attachment img {{ background: #171b1e; }}
    }}
    @media (max-width: 720px) {{
      header {{ padding: 0 15px; }}
      .layout {{ display: block; }}
      aside {{ border-right: 0; border-bottom: 1px solid var(--border); }}
      .aside-title {{ padding: 14px 15px 8px; }}
      .group-list {{ display: flex; overflow-x: auto; padding: 0 10px 12px; gap: 8px; }}
      .group-link {{ min-width: 205px; min-height: 72px; padding: 11px 12px; border: 1px solid var(--border); border-radius: 6px; }}
      .group-link.active {{ box-shadow: inset 0 -3px var(--accent); }}
      .conversation-head {{ top: 59px; padding: 14px 16px; }}
      .messages {{ padding: 8px 16px 36px; }}
      .message {{ grid-template-columns: 36px minmax(0, 1fr); gap: 10px; }}
      .avatar {{ width: 36px; height: 36px; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="brand">AiQQ 群消息</div>
    <a class="refresh" href="{refresh_url}" title="刷新当前群消息">刷新</a>
  </header>
  <div class="layout">
    <aside>
      <div class="aside-title">群组</div>
      <div class="group-list">{group_links}</div>
    </aside>
    <main>
      <div class="conversation-head">
        <h1>{_escape(group_title)}</h1>
        <div class="meta">{group_meta}</div>
      </div>
      <div class="messages">{message_items}{older_link}</div>
    </main>
  </div>
</body>
</html>"""


def _render_group_link(group: GroupMessageSummary, selected: bool) -> str:
    query = urlencode({"group": group.group_openid})
    active = " active" if selected else ""
    latest_author = group.latest_username or _short_id(group.latest_member_openid)
    latest_content = _preview(group.latest_content) or "[非文本消息]"
    return (
        f'<a class="group-link{active}" href="?{_escape(query)}">'
        '<div class="group-row">'
        f'<span class="group-name">群组 {_escape(_short_id(group.group_openid))}</span>'
        f'<span class="count">{group.message_count}</span>'
        "</div>"
        f'<div class="preview">{_escape(latest_author)}：{_escape(latest_content)}</div>'
        "</a>"
    )


def _render_message(message: StoredGroupMessage) -> str:
    author = message.username or _short_id(message.member_openid) or "未知用户"
    initial = author[:1].upper()
    role = {
        "owner": "群主",
        "admin": "管理员",
        "member": "成员",
    }.get(message.member_role, "机器人" if message.is_bot else "成员")
    content = message.content.strip()
    content_html = _escape(content).replace("\n", "<br>") if content else ""
    if not content_html:
        content_html = '<span class="body muted">[非文本消息]</span>'

    elements = list(_element_texts(message.payload.get("msg_elements")))
    elements_html = ""
    if elements:
        joined = "\n\n".join(elements[:MAX_VISIBLE_ELEMENTS])
        elements_html = f'<div class="elements">{_escape(joined)}</div>'

    attachments_html = _render_attachments(message.payload.get("attachments"))
    return (
        '<article class="message">'
        f'<div class="avatar">{_escape(initial)}</div>'
        "<div>"
        '<div class="message-head">'
        f'<span class="author">{_escape(author)}</span>'
        f'<span class="role">{_escape(role)}</span>'
        f'<time>{_escape(_display_time(message.sent_at or message.received_at))}</time>'
        "</div>"
        f'<div class="body">{content_html}</div>'
        f"{elements_html}{attachments_html}"
        "</div>"
        "</article>"
    )


def _render_attachments(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    items = []
    for attachment in value[:20]:
        if not isinstance(attachment, dict):
            continue
        url = attachment.get("url")
        if not _safe_http_url(url):
            continue
        filename = attachment.get("filename")
        label = filename if isinstance(filename, str) and filename else "查看附件"
        content_type = attachment.get("content_type")
        if isinstance(content_type, str) and content_type.startswith("image/"):
            media = (
                f'<img src="{_escape(url)}" alt="{_escape(label)}" loading="lazy">'
            )
        else:
            media = ""
        items.append(
            '<a class="attachment" target="_blank" rel="noopener noreferrer" '
            f'href="{_escape(url)}">{media}<span>{_escape(label)}</span></a>'
        )
    return f'<div class="attachments">{"".join(items)}</div>' if items else ""


def _element_texts(value: Any, depth: int = 0) -> Iterable[str]:
    if depth >= MAX_ELEMENT_DEPTH or not isinstance(value, list):
        return
    for element in value:
        if not isinstance(element, dict):
            continue
        content = element.get("content")
        if isinstance(content, str) and content.strip():
            yield content.strip()
        nested = element.get("msg_elements")
        yield from _element_texts(nested, depth + 1)


def _safe_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _display_time(value: str) -> str:
    if not value:
        return "时间未知"
    return value.replace("T", " ", 1).replace("+08:00", "")


def _preview(value: str, limit: int = 45) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[:limit] + "..."


def _short_id(value: str) -> str:
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-4:]}"


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)
