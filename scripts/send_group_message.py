#!/usr/bin/env python3
"""Send one proactive group message or contextual reply through AiQQ."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from dotenv import load_dotenv


DEFAULT_ENDPOINT = "http://127.0.0.1:8787/internal/group-messages"
EXIT_ARGUMENT = 2
EXIT_UNAVAILABLE = 3
EXIT_REJECTED = 4
EXIT_UNKNOWN = 5


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send one proactive QQ group message."
    )
    parser.add_argument("--group", required=True, help="Target group OpenID.")
    parser.add_argument(
        "--reply-to",
        help="Reply to a specific @ message from the last five minutes.",
    )
    parser.add_argument("--text", help="Message text; omit to read standard input.")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def load_admin_token() -> str:
    token = os.environ.get("AIQQ_ADMIN_TOKEN", "").strip()
    if token:
        return token
    file_name = os.environ.get("AIQQ_ADMIN_TOKEN_FILE", "").strip()
    if not file_name:
        return ""
    path = Path(file_name).expanduser()
    try:
        if path.stat().st_mode & 0o077:
            raise ValueError("AIQQ_ADMIN_TOKEN_FILE permissions must be 0600")
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError("cannot read AIQQ_ADMIN_TOKEN_FILE") from exc


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    endpoint = urlsplit(args.endpoint)
    if endpoint.scheme != "http" or endpoint.hostname not in {"127.0.0.1", "::1"}:
        print("错误：管理接口必须使用本机 HTTP 地址。", file=sys.stderr)
        return EXIT_ARGUMENT
    content = args.text if args.text is not None else sys.stdin.read()
    if not content.strip() or len(content) > 2000:
        print("错误：消息正文必须包含 1 至 2000 个字符。", file=sys.stderr)
        return EXIT_ARGUMENT
    try:
        token = load_admin_token()
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_ARGUMENT
    if not token:
        print("错误：未配置 AIQQ_ADMIN_TOKEN 或 Token 文件。", file=sys.stderr)
        return EXIT_ARGUMENT
    payload = {"group_openid": args.group, "content": content.strip()}
    if args.reply_to:
        payload["reply_to_message_id"] = args.reply_to
    request = Request(
        args.endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            result = json.load(response)
    except HTTPError as exc:
        try:
            result = json.loads(exc.read().decode("utf-8"))
            message = result["error"]["message"]
        except Exception:
            message = f"HTTP {exc.code}"
        print(f"发送失败：{message}", file=sys.stderr)
        return EXIT_REJECTED
    except URLError as exc:
        print(f"机器人管理接口不可用：{exc.reason}", file=sys.stderr)
        return EXIT_UNAVAILABLE
    except Exception as exc:
        print(f"未知错误：{type(exc).__name__}", file=sys.stderr)
        return EXIT_UNKNOWN
    print(f"发送成功：message_id={result['message_id']} sent_at={result['sent_at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
