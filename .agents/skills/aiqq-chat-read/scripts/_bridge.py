"""Small standard-library client for the server-bound, per-turn bridge."""

from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


MAX_RESULT_BYTES = 64 * 1024 * 1024


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def read(operation: str, arguments: dict) -> None:
    try:
        base = os.environ.get("AIQQ_CHAT_READ_URL", "")
        token = os.environ.get("AIQQ_CHAT_READ_TOKEN", "")
        parsed = urlsplit(base)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                or parsed.port is None or parsed.username is not None
                or parsed.password is not None or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment or not token
                or operation not in {"history", "image"}):
            _print_error("unavailable", "本轮读取接口不可用。")
            return
        body = json.dumps(arguments, ensure_ascii=False).encode("utf-8")
        request = Request(
            base.rstrip("/") + "/" + operation, data=body, method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
        )
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        with opener.open(request, timeout=90) as response:
            raw = response.read(MAX_RESULT_BYTES + 1)
        if len(raw) > MAX_RESULT_BYTES:
            _print_error("capacity", "完整读取结果超过本轮容量，请缩小查询范围。")
            return
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("invalid bridge response")
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    except HTTPError:
        _print_error("unavailable", "本轮读取请求未被接受，请核对记录和附件编号。")
    except (URLError, TimeoutError, OSError, ValueError):
        _print_error("unavailable", "本轮读取接口暂不可用，请稍后重试。")


def _print_error(kind: str, message: str) -> None:
    print(json.dumps({"status": "error", "error_kind": kind, "message": message}, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit("Use read_history.py or read_image.py")
