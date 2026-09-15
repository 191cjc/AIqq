"""Authenticated group-message viewer with proactive text sending."""

from __future__ import annotations

import base64
import binascii
import hmac
import html
import logging
import secrets
from collections.abc import Iterable, Sequence
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

from aiohttp import web

from aiqq.logic.ports import GroupMessageSender


logger = logging.getLogger(__name__)
MESSAGE_PAGE_SIZE = 50
MAX_ELEMENT_DEPTH = 4
MAX_VISIBLE_ELEMENTS = 50
MAX_OUTBOUND_MESSAGE_CHARS = 2000


class GroupMessageReader(Protocol):
    async def groups(self, *, limit: int = 200) -> Sequence[Any]: ...

    async def recent(
        self,
        group_id: str,
        *,
        limit: int = 20,
        before_record_id: int | None = None,
        exclude_message_id: str = "",
    ) -> Sequence[Any]: ...


class GroupMessageViewer:
    def __init__(
        self,
        repository: GroupMessageReader,
        sender: GroupMessageSender,
        *,
        username: str,
        password: str,
    ) -> None:
        self._repository = repository
        self._sender = sender
        self._username = username
        self._password = password
        self._csrf_token = secrets.token_urlsafe(32)

    @property
    def is_configured(self) -> bool:
        return bool(self._username and self._password)

    async def serve(self, request: web.Request) -> web.Response:
        authentication_error = self._authentication_error(request)
        if authentication_error is not None:
            return authentication_error
        try:
            groups = tuple(await self._repository.groups())
            selected_group = str(request.rel_url.query.get("group", ""))[:256]
            available_groups = {group.group_openid for group in groups}
            if selected_group not in available_groups:
                selected_group = groups[0].group_openid if groups else ""
            before_record_id = _positive_int(
                request.rel_url.query.get("before")
            )
            messages = (
                tuple(
                    await self._repository.recent(
                        selected_group,
                        limit=MESSAGE_PAGE_SIZE,
                        before_record_id=before_record_id,
                    )
                )
                if selected_group
                else ()
            )
        except Exception as exc:
            logger.warning(
                "event=group_message_view_failed error_type=%s", type(exc).__name__
            )
            return _response("群消息暂时无法读取。", 503, "text/plain")
        return _response(
            _render_page(
                groups,
                selected_group,
                messages,
                csrf_token=self._csrf_token,
                send_status=str(request.rel_url.query.get("send", "")),
            ),
            200,
            "text/html",
        )

    async def send_message(self, request: web.Request) -> web.Response:
        authentication_error = self._authentication_error(request)
        if authentication_error is not None:
            return authentication_error
        try:
            form = await request.post()
        except (ValueError, TypeError):
            return _response("请求表单无效。", 400, "text/plain")
        supplied_csrf = form.get("csrf_token")
        if not isinstance(supplied_csrf, str) or not hmac.compare_digest(
            supplied_csrf, self._csrf_token
        ):
            return _response("请求验证失败，请刷新页面后重试。", 403, "text/plain")
        group_id = form.get("group_openid")
        content = form.get("content")
        if not isinstance(group_id, str):
            return _redirect("", "invalid_group")
        group_id = group_id[:256]
        try:
            groups = tuple(await self._repository.groups())
        except Exception as exc:
            logger.warning(
                "event=group_message_send_groups_failed error_type=%s",
                type(exc).__name__,
            )
            return _redirect(group_id, "failed")
        if group_id not in {str(group.group_openid) for group in groups}:
            return _redirect("", "invalid_group")
        if (
            not isinstance(content, str)
            or not content.strip()
            or len(content) > MAX_OUTBOUND_MESSAGE_CHARS
        ):
            return _redirect(group_id, "invalid")
        try:
            await self._sender.send_proactive_text(
                group_id=group_id,
                content=content.strip(),
                origin="message_viewer",
            )
        except Exception as exc:
            logger.warning(
                "event=group_message_view_send_failed error_type=%s",
                type(exc).__name__,
            )
            return _redirect(group_id, "failed")
        return _redirect(group_id, "sent")

    def _authentication_error(self, request: web.Request) -> web.Response | None:
        if not self.is_configured:
            return _response("群消息查看页面尚未配置管理员口令。", 503, "text/plain")
        if self._is_authorized(request.headers.get("Authorization", "")):
            return None
        response = _response("需要管理员身份验证。", 401, "text/plain")
        response.headers["WWW-Authenticate"] = (
            'Basic realm="AiQQ group messages", charset="UTF-8"'
        )
        return response

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
        return hmac.compare_digest(username, self._username) and hmac.compare_digest(
            password, self._password
        )


def register_group_message_routes(
    application: web.Application, viewer: GroupMessageViewer
) -> None:
    application.router.add_get("/group-messages", viewer.serve)
    application.router.add_post("/group-messages", viewer.send_message)


def _response(text: str, status: int, content_type: str) -> web.Response:
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


def _redirect(group_id: str, send_status: str) -> web.Response:
    query = {"send": send_status}
    if group_id:
        query["group"] = group_id
    response = _response("", 303, "text/plain")
    response.headers["Location"] = f"?{urlencode(query)}"
    return response


def _render_page(
    groups: Sequence[Any],
    selected_group: str,
    messages: Sequence[Any],
    *,
    csrf_token: str,
    send_status: str,
) -> str:
    selected_summary = next(
        (group for group in groups if group.group_openid == selected_group), None
    )
    group_links = "".join(
        _render_group_link(group, group.group_openid == selected_group)
        for group in groups
    ) or '<div class="empty compact">尚未收到群消息。</div>'
    message_items = "".join(
        _render_message(message) for message in reversed(messages)
    ) or '<div class="empty">该群暂无可显示的消息记录。</div>'
    title = f"群组 {_short_id(selected_group)}" if selected_group else "群消息"
    meta = ""
    if selected_summary is not None:
        meta = (
            f"{selected_summary.message_count} 条已记录消息 · "
            f"OpenID {_escape(selected_summary.group_openid)}"
        )
    older_link = ""
    if len(messages) == MESSAGE_PAGE_SIZE:
        oldest_id = min(message.record_id for message in messages)
        query = urlencode({"group": selected_group, "before": oldest_id})
        older_link = f'<a class="older" href="?{_escape(query)}">查看更早消息</a>'
    refresh_query = urlencode({"group": selected_group}) if selected_group else ""
    refresh_url = f"?{_escape(refresh_query)}" if refresh_query else "?"
    composer = _render_composer(selected_group, csrf_token, send_status)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AiQQ 群消息</title>
  <style>
    :root {{ color-scheme:light; font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
      --page:#f2f4f5; --surface:#fff; --subtle:#f8f9fa; --text:#172026;
      --muted:#66727a; --line:#d9dee2; --accent:#176b4d; --selected:#e9f3ee;
      --action:#176b4d; --action-text:#fff; --danger:#a12626; }}
    * {{ box-sizing:border-box; }} html,body {{ height:100%; }} body {{ margin:0; display:grid;
      grid-template-rows:58px minmax(0,1fr); overflow:hidden; color:var(--text);
      background:var(--page); letter-spacing:0; }}
    header {{ position:relative; z-index:3; height:58px; display:flex; align-items:center;
      justify-content:space-between; padding:0 22px; border-bottom:1px solid var(--line); background:var(--surface); }}
    .brand {{ font-size:17px; font-weight:750; }} .refresh {{ width:38px; height:38px; display:grid;
      place-items:center; border:1px solid var(--line); border-radius:6px; color:var(--accent);
      font-size:21px; text-decoration:none; }}
    .layout {{ display:grid; grid-template-columns:minmax(230px,300px) minmax(0,1fr);
      width:100%; height:100%; min-height:0; max-width:1500px; margin:auto;
      overflow:hidden; background:var(--surface); }}
    aside {{ min-height:0; overflow-y:auto; border-right:1px solid var(--line); background:var(--subtle); }}
    .aside-title {{ padding:18px 18px 10px; color:var(--muted); font-size:12px; font-weight:700; }}
    .group-link {{ display:block; min-height:76px; padding:13px 18px; border-bottom:1px solid var(--line); text-decoration:none; }}
    .group-link:hover,.group-link.active {{ background:var(--selected); }}
    .group-link.active {{ box-shadow:inset 3px 0 var(--accent); }}
    .group-row,.message-head {{ display:flex; flex-wrap:wrap; justify-content:space-between; gap:8px; }}
    .group-name,.author {{ font-size:14px; font-weight:750; }} .count {{ color:var(--accent); font-size:12px; }}
    .preview {{ margin-top:7px; color:var(--muted); font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
    main {{ min-width:0; min-height:0; display:flex; flex-direction:column; }} .conversation-head {{ position:relative; z-index:2;
      padding:17px 24px; border-bottom:1px solid var(--line); background:var(--surface); }}
    h1 {{ margin:0; font-size:18px; }} .meta,.role,time {{ color:var(--muted); font-size:11px; }}
    .meta {{ margin-top:5px; overflow-wrap:anywhere; }} .messages {{ width:100%; max-width:920px;
      min-height:0; flex:1; margin:0 auto; padding:18px 24px 28px; overflow-y:auto; }}
    .message {{ display:grid; grid-template-columns:42px minmax(0,1fr); gap:12px; padding:16px 0; border-bottom:1px solid var(--line); }}
    .avatar {{ width:42px; height:42px; display:grid; place-items:center; border-radius:6px;
      background:var(--selected); color:var(--accent); font-weight:800; }}
    .message-head {{ justify-content:flex-start; align-items:baseline; }}
    .body {{ margin-top:7px; font-size:14px; line-height:1.65; overflow-wrap:anywhere; }}
    .body.muted {{ color:var(--muted); font-style:italic; }} .elements {{ margin-top:10px;
      padding:10px 12px; border-left:3px solid var(--accent); background:var(--subtle);
      font-size:13px; white-space:pre-wrap; overflow-wrap:anywhere; }}
    .attachments {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:12px; }}
    .attachment {{ width:min(240px,100%); border:1px solid var(--line); border-radius:6px;
      overflow:hidden; background:var(--subtle); text-decoration:none; }}
    .attachment img {{ display:block; width:100%; max-height:220px; object-fit:contain; }}
    .attachment span {{ display:block; padding:8px 10px; color:var(--muted); font-size:11px; overflow-wrap:anywhere; }}
    .empty {{ padding:54px 24px; color:var(--muted); text-align:center; }} .empty.compact {{ padding:20px 18px; text-align:left; }}
    .older {{ display:block; width:fit-content; margin:22px auto; padding:9px 14px;
      border:1px solid var(--accent); border-radius:6px; color:var(--accent); text-decoration:none; font-weight:700; }}
    .composer-wrap {{ position:relative; z-index:2; flex:0 0 auto; border-top:1px solid var(--line);
      background:var(--surface); }} .composer-inner {{ max-width:920px; margin:auto; padding:12px 24px 16px; }}
    .send-feedback {{ min-height:18px; margin-bottom:7px; color:var(--accent); font-size:12px; }}
    .send-feedback.error {{ color:var(--danger); }} .composer {{ display:grid;
      grid-template-columns:minmax(0,1fr) 44px; gap:9px; align-items:end; }}
    .composer textarea {{ width:100%; min-height:44px; max-height:144px; resize:vertical;
      padding:10px 12px; border:1px solid var(--line); border-radius:6px; outline:0;
      background:var(--subtle); color:var(--text); font:inherit; font-size:14px; line-height:1.45; }}
    .composer textarea:focus {{ border-color:var(--accent); box-shadow:0 0 0 2px var(--selected); }}
    .send-button {{ width:44px; height:44px; display:grid; place-items:center; border:0;
      border-radius:6px; background:var(--action); color:var(--action-text); font-size:22px;
      cursor:pointer; }} .send-button:hover {{ filter:brightness(.94); }}
    .send-button:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}
    @media (prefers-color-scheme:dark) {{ :root {{ color-scheme:dark; --page:#15191c;
      --surface:#1d2226; --subtle:#242a2e; --text:#edf1f3; --muted:#a6b0b7;
      --line:#3a4248; --accent:#6ec39e; --selected:#283a32; --action:#6ec39e;
      --action-text:#10251b; --danger:#ff8a80; }} }}
    @media (max-width:720px) {{ header {{ padding:0 15px; }} .layout {{ display:flex; flex-direction:column; }}
      aside {{ flex:0 0 auto; overflow:hidden; border-right:0; border-bottom:1px solid var(--line); }}
      main {{ flex:1 1 auto; }} .group-list {{ display:flex;
      gap:8px; padding:0 10px 12px; overflow-x:auto; }} .group-link {{ min-width:205px;
      border:1px solid var(--line); border-radius:6px; }} .conversation-head {{ padding:14px 16px; }}
      .messages {{ padding:8px 16px 22px; }} .message {{ grid-template-columns:36px minmax(0,1fr); }}
      .avatar {{ width:36px; height:36px; }} .composer-inner {{ padding:10px 16px 12px; }} }}
  </style>
</head>
<body>
  <header><div class="brand">AiQQ 群消息</div><a class="refresh" href="{refresh_url}" aria-label="刷新" title="刷新">&#8635;</a></header>
  <div class="layout"><aside><div class="aside-title">群组</div><div class="group-list">{group_links}</div></aside>
  <main><div class="conversation-head"><h1>{_escape(title)}</h1><div class="meta">{meta}</div></div>
  <div class="messages">{message_items}{older_link}</div>{composer}</main></div>
</body>
</html>"""


def _render_composer(selected_group: str, csrf_token: str, send_status: str) -> str:
    if not selected_group:
        return ""
    feedbacks = {
        "sent": ("消息已发送。", ""),
        "invalid": ("请输入 1 至 2000 个字符。", " error"),
        "invalid_group": ("目标群不可用。", " error"),
        "failed": ("发送失败，请检查主动消息权限或频次后重试。", " error"),
    }
    feedback, feedback_class = feedbacks.get(send_status, ("", ""))
    action = f"?{urlencode({'group': selected_group})}"
    return (
        '<div class="composer-wrap"><div class="composer-inner">'
        f'<div class="send-feedback{feedback_class}" role="status">{_escape(feedback)}</div>'
        f'<form class="composer" method="post" action="{_escape(action)}">'
        f'<input type="hidden" name="csrf_token" value="{_escape(csrf_token)}">'
        f'<input type="hidden" name="group_openid" value="{_escape(selected_group)}">'
        f'<textarea name="content" maxlength="{MAX_OUTBOUND_MESSAGE_CHARS}" required '
        'placeholder="输入消息..." aria-label="消息内容"></textarea>'
        '<button class="send-button" type="submit" aria-label="发送" title="发送">&#8593;</button>'
        '</form></div></div>'
    )


def _render_group_link(group: Any, selected: bool) -> str:
    query = urlencode({"group": group.group_openid})
    active = " active" if selected else ""
    author = group.latest_username or _short_id(group.latest_member_openid)
    preview = _preview(group.latest_content) or "[非文本消息]"
    return (
        f'<a class="group-link{active}" href="?{_escape(query)}">'
        f'<div class="group-row"><span class="group-name">群组 {_escape(_short_id(group.group_openid))}</span>'
        f'<span class="count">{group.message_count}</span></div>'
        f'<div class="preview">{_escape(author)}：{_escape(preview)}</div></a>'
    )


def _render_message(message: Any) -> str:
    author = message.username or _short_id(message.member_openid) or "未知用户"
    role = {
        "owner": "群主",
        "admin": "管理员",
        "member": "成员",
    }.get(message.member_role, "机器人" if message.is_bot else "成员")
    content = message.content.strip()
    content_html = _escape(content).replace("\n", "<br>") if content else '<span class="body muted">[非文本消息]</span>'
    elements = list(_element_texts(message.payload.get("msg_elements")))
    elements_html = (
        f'<div class="elements">{_escape(chr(10).join(elements[:MAX_VISIBLE_ELEMENTS]))}</div>'
        if elements
        else ""
    )
    attachments = _render_attachments(message.payload.get("attachments"))
    return (
        '<article class="message">'
        f'<div class="avatar">{_escape(author[:1].upper())}</div><div>'
        f'<div class="message-head"><span class="author">{_escape(author)}</span>'
        f'<span class="role">{_escape(role)}</span><time>{_escape(_display_time(message.sent_at or message.received_at))}</time></div>'
        f'<div class="body">{content_html}</div>{elements_html}{attachments}</div></article>'
    )


def _render_attachments(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    items: list[str] = []
    for attachment in value[:20]:
        if not isinstance(attachment, dict):
            continue
        url = attachment.get("url")
        if not _safe_http_url(url):
            continue
        filename = attachment.get("filename")
        label = filename if isinstance(filename, str) and filename else "查看附件"
        content_type = attachment.get("content_type")
        media = (
            f'<img src="{_escape(url)}" alt="{_escape(label)}" loading="lazy">'
            if isinstance(content_type, str) and content_type.startswith("image/")
            else ""
        )
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
        yield from _element_texts(element.get("msg_elements"), depth + 1)


def _safe_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def _positive_int(value: str | None) -> int | None:
    if value is None or not value.isdecimal():
        return None
    number = int(value)
    return number if number > 0 else None


def _display_time(value: str) -> str:
    return value.replace("T", " ", 1).replace("+08:00", "") if value else "时间未知"


def _preview(value: str, limit: int = 45) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[:limit] + "..."


def _short_id(value: str) -> str:
    return value if len(value) <= 12 else f"{value[:6]}...{value[-4:]}"


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)
