"""Per-turn authenticated bridges and confined Codex launcher for chat reads."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import signal
import stat
import shlex
import shutil
from typing import Any

from aiohttp import web
import httpx

from aiqq.logic.chat_read import ChatReadSession
from aiqq.services.ai import chat_isolation

logger = logging.getLogger(__name__)

MAX_VISUAL_INPUTS = 9
MAX_TOOL_ROUNDS = 8
MAX_MODEL_REQUESTS = 24
MAX_MODEL_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_SEARCH_REQUESTS = 16
# Native web requests include the complete conversation, including viewed
# image inputs. Match the model request capacity instead of limiting queries.
MAX_SEARCH_REQUEST_BYTES = 64 * 1024 * 1024
MAX_SEARCH_RESPONSE_BYTES = 16 * 1024 * 1024
SEARCH_TIMEOUT_SECONDS = 60
HOP_HEADERS = {"host", "content-length", "connection", "transfer-encoding", "te",
               "trailer", "upgrade", "proxy-authorization", "proxy-connection",
               "authorization", "content-encoding", "accept-encoding"}


class ChatRuntime:
    def __init__(self, *, session: ChatReadSession, work: Path, runtime: Path,
                 cli: str, api_key: str, base_url: str, model: str,
                 enable_web_search: bool = False) -> None:
        self.session, self.work, self.runtime = session, work, runtime
        self.cli, self.api_key, self.base_url, self.model = cli, api_key, base_url, model
        self.enable_web_search = enable_web_search
        self.model_token = secrets.token_urlsafe(32)
        self.read_token = secrets.token_urlsafe(32)
        self.url = ""
        self.launcher = runtime / "chat-codex"
        self._runner: web.AppRunner | None = None
        self._client: httpx.AsyncClient | None = None
        self._handlers: set[asyncio.Task] = set()
        self._requests = 0
        self._search_requests = 0
        self._images: dict[str, Path] = {}
        self._supervisor: tuple[int, str] | None = None
        self._history_context: list[dict[str, Any]] = []

    async def __aenter__(self) -> "ChatRuntime":
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_post("/v1/responses", self._proxy)
        app.router.add_post("/v1/alpha/search", self._search)
        app.router.add_post("/history", self._read)
        app.router.add_post("/image", self._read)
        self._runner = web.AppRunner(app, access_log=None, shutdown_timeout=1)
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=15), trust_env=False)
        try:
            await self._runner.setup()
            site = web.TCPSite(self._runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            self.url = f"http://127.0.0.1:{port}"
            self._prepare_launcher(port)
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *_args) -> None:
        self.stop_children()
        for task in tuple(self._handlers):
            task.cancel()
        if self._handlers:
            await asyncio.gather(*tuple(self._handlers), return_exceptions=True)
        if self._runner is not None:
            await self._runner.cleanup()
        if self._client is not None:
            await self._client.aclose()

    def stop_children(self) -> None:
        """Kill tool descendants before the SDK kills its CLI process on abort."""
        if self._supervisor is None:
            return
        parent, start = self._supervisor
        if _process_start(parent) != start:
            return
        parents = {}
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                parents[int(entry.name)] = int(fields[1])
            except (OSError, ValueError, IndexError):
                continue
        descendants = {parent}
        while True:
            expanded = descendants | {pid for pid, ppid in parents.items() if ppid in descendants}
            if expanded == descendants:
                break
            descendants = expanded
        for pid in descendants - {parent}:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)

    def _capture_supervisor(self) -> None:
        # First called before returning the first model response, so no tool
        # has yet had an opportunity to mutate the child's marker file.
        if self._supervisor is not None:
            return
        try:
            pid = int((self.runtime / ".chat-cli-pid").read_text())
            environment = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
            if ("AIQQ_CHAT_READ_TOKEN=" + self.read_token).encode() in environment:
                start = _process_start(pid)
                if start:
                    self._supervisor = (pid, start)
        except (OSError, ValueError):
            pass

    def _authorized(self, request: web.Request, token: str) -> bool:
        return hmac.compare_digest(request.headers.get("Authorization", ""), "Bearer " + token)

    async def _read(self, request: web.Request) -> web.Response:
        if not self._authorized(request, self.read_token):
            raise web.HTTPUnauthorized()
        task = asyncio.current_task()
        self._handlers.add(task)
        try:
            if request.content_length and request.content_length > 16 * 1024:
                raise web.HTTPRequestEntityTooLarge(max_size=16384, actual_size=request.content_length)
            raw = await request.content.read(16 * 1024 + 1)
            chunks = [raw]
            total = len(raw)
            while raw and total <= 16 * 1024:
                raw = await request.content.read(16 * 1024 + 1 - total)
                chunks.append(raw)
                total += len(raw)
            if total > 16 * 1024:
                raise web.HTTPRequestEntityTooLarge(max_size=16384, actual_size=total)
            payload = json.loads(b"".join(chunks))
            if not isinstance(payload, dict):
                raise web.HTTPBadRequest()
            if request.path == "/history":
                result = await self.session.read_history(**payload)
                if result.get("status") != "error":
                    # Native shell output has its own token truncation. Preserve
                    # the full page in the parent and inject it into the model
                    # request directly; stdout carries only a bounded receipt.
                    encoded = json.dumps(result, ensure_ascii=False)
                    if len(encoded.encode("utf-8")) > 32 * 1024 * 1024:
                        result = {"status": "error", "error_kind": "capacity",
                                  "message": "完整历史页超过本轮容量，请缩小查询范围。"}
                    else:
                        self._history_context.append(json.loads(encoded))
                        result = {"status": "ok", "full_records_delivery": "model_context",
                                  "page_number": len(self._history_context),
                                  "message_count": len(result.get("messages", [])),
                                  **{key: result[key] for key in (
                                      "next_cursor", "next_version_cursor", "next_event_cursor",
                                      "has_more", "has_more_versions", "has_more_events",
                                      "pages_read", "pages_remaining") if key in result}}
            else:
                if set(payload) - {"record_id", "attachment_index", "version_id"}:
                    raise web.HTTPBadRequest()
                metadata, images = await self.session.read_image(**payload)
                staged = []
                for image in images:
                    path = self._stage_image(image)
                    staged.append({"path": str(path), "mime_type": image.mime_type,
                                   "width": image.width, "height": image.height})
                result = {**metadata, "images": staged}
            return web.json_response(result, dumps=lambda value: json.dumps(value, ensure_ascii=False))
        except OSError:
            raise web.HTTPBadRequest(text="temporary image directory unavailable") from None
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text="invalid read parameters") from None
        finally:
            self._handlers.discard(task)

    def _stage_image(self, image) -> Path:
        # The child can write in its own work directory. Resolve each component
        # through directory FDs so it cannot redirect this privileged writer
        # into another group's directory with a symlink or rename race.
        digest = hashlib.sha256(image.data).hexdigest()
        root_fd = os.open(self.work, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            with contextlib.suppress(FileExistsError):
                os.mkdir("read-images", mode=0o700, dir_fd=root_fd)
            folder_fd = os.open("read-images", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=root_fd)
            try:
                cached = self._images.get(digest)
                if cached is not None:
                    try:
                        fd = os.open(cached.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                     dir_fd=folder_fd)
                        with os.fdopen(fd, "rb") as handle:
                            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                                raise OSError("cached image is not a regular file")
                            data = handle.read(len(image.data) + 1)
                        if hashlib.sha256(data).hexdigest() == digest:
                            return cached
                    except OSError:
                        pass
                elif len(self._images) >= MAX_VISUAL_INPUTS:
                    raise web.HTTPBadRequest(text="visual input budget exceeded")
                suffix = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[image.mime_type]
                name = f"{secrets.token_hex(16)}.{suffix}"
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=folder_fd)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(image.data)
                path = self.work / "read-images" / name
                self._images[digest] = path
                return path
            finally:
                os.close(folder_fd)
        finally:
            os.close(root_fd)

    async def _proxy(self, request: web.Request) -> web.StreamResponse:
        if not self._authorized(request, self.model_token):
            raise web.HTTPUnauthorized()
        self._capture_supervisor()
        self._requests += 1
        if self._requests > MAX_MODEL_REQUESTS:
            raise web.HTTPTooManyRequests(text="chat model request budget exceeded")
        task = asyncio.current_task()
        self._handlers.add(task)
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or payload.get("model") != self.model:
                raise web.HTTPBadRequest(text="unexpected model")
            if self._history_context:
                inputs = payload.get("input")
                if not isinstance(inputs, list):
                    raise web.HTTPBadRequest(text="unexpected model input")
                payload["input"] = [*inputs, {"type": "message", "role": "user", "content": [{
                    "type": "input_text", "text": json.dumps({
                        "untrusted_chat_read_results": self._history_context,
                        "scope": "complete same-group history pages requested by the read_history tool",
                    }, ensure_ascii=False),
                }]}]
            if any(tool.get("type") == "image_generation" for tool in payload.get("tools", []) if isinstance(tool, dict)):
                raise web.HTTPBadRequest(text="image generation is not a chat read tool")
            if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 64 * 1024 * 1024:
                return _context_capacity_response()
            headers = {key: value for key, value in request.headers.items() if key.lower() not in HOP_HEADERS}
            headers["Authorization"] = "Bearer " + self.api_key
            # Genuine SDK headers are forwarded; no client identity is forged.
            async with self._client.stream("POST", self.base_url.rstrip("/") + "/responses", json=payload, headers=headers) as upstream:
                response = web.StreamResponse(status=upstream.status_code,
                    headers={key: value for key, value in upstream.headers.items() if key.lower() not in HOP_HEADERS})
                await response.prepare(request)
                total = 0
                async for chunk in upstream.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_MODEL_RESPONSE_BYTES:
                        raise web.HTTPBadGateway(text="model response too large")
                    await response.write(chunk)
                await response.write_eof()
                return response
        except web.HTTPRequestEntityTooLarge:
            return _context_capacity_response()
        except (ValueError, TypeError):
            raise web.HTTPBadRequest(text="invalid model request") from None
        except httpx.HTTPError:
            raise web.HTTPBadGateway(text="model upstream unavailable") from None
        finally:
            self._handlers.discard(task)

    async def _search(self, request: web.Request) -> web.Response:
        # Codex's web.run uses a standalone endpoint, not a Responses payload.
        # Keep it opaque: no model replacement or chat-history injection here.
        if not self._authorized(request, self.model_token):
            return self._search_error(401, "unauthorized")
        if not self.enable_web_search:
            return self._search_error(403, "disabled")
        self._search_requests += 1
        if self._search_requests > MAX_SEARCH_REQUESTS:
            return self._search_error(429, "request_budget_exceeded", self._search_requests)
        request_number = self._search_requests
        task = asyncio.current_task()
        self._handlers.add(task)
        try:
            async with asyncio.timeout(SEARCH_TIMEOUT_SECONDS):
                if request.content_length is not None and request.content_length > MAX_SEARCH_REQUEST_BYTES:
                    return self._search_error(413, "request_too_large", request_number)
                body = bytearray()
                async for chunk in request.content.iter_chunked(64 * 1024):
                    if len(body) + len(chunk) > MAX_SEARCH_REQUEST_BYTES:
                        return self._search_error(413, "request_too_large", request_number)
                    body.extend(chunk)
                try:
                    if not isinstance(json.loads(body), dict):
                        return self._search_error(400, "invalid_request", request_number)
                except (ValueError, UnicodeError):
                    return self._search_error(400, "invalid_request", request_number)
                headers = {key: value for key, value in request.headers.items() if key.lower() not in HOP_HEADERS}
                headers["Authorization"] = "Bearer " + self.api_key
                # Preserve the genuine CLI's headers and exact JSON bytes. The
                # provider key is substituted only in this parent-owned client.
                async with self._client.stream(
                    "POST", self.base_url.rstrip("/") + "/alpha/search",
                    content=bytes(body), headers=headers,
                    timeout=httpx.Timeout(SEARCH_TIMEOUT_SECONDS, connect=15),
                    follow_redirects=False,
                ) as upstream:
                    output = bytearray()
                    async for chunk in upstream.aiter_bytes(chunk_size=64 * 1024):
                        if len(output) + len(chunk) > MAX_SEARCH_RESPONSE_BYTES:
                            return self._search_error(502, "response_too_large", request_number)
                        output.extend(chunk)
                    self._log_search(upstream.status_code,
                        "success" if upstream.is_success else "upstream_http_error", request_number)
                    return web.Response(status=upstream.status_code, body=bytes(output),
                        headers={key: value for key, value in upstream.headers.items() if key.lower() not in HOP_HEADERS})
        except (TimeoutError, httpx.TimeoutException):
            return self._search_error(504, "timeout", request_number)
        except httpx.HTTPError:
            return self._search_error(502, "upstream_unavailable", request_number)
        finally:
            self._handlers.discard(task)

    def _search_error(self, status: int, reason: str, request_number: int = 0) -> web.Response:
        self._log_search(status, reason, request_number)
        return web.json_response({"error": {"code": reason,
            "message": "Chat web search unavailable."}}, status=status)

    @staticmethod
    def _log_search(status: int, reason: str, request_number: int) -> None:
        log = logger.info if status < 300 else logger.warning
        log("event=chat_web_search_finished status=%s reason=%s request_number=%s",
            status, reason, request_number)

    def _prepare_launcher(self, port: int) -> None:
        program = self.runtime / "chat_isolation.py"
        shutil.copyfile(chat_isolation.__file__, program)
        (self.runtime / "tmp").mkdir(mode=0o700)
        system_paths = ["/usr/bin", "/usr/lib", "/usr/lib64", "/bin", "/lib", "/lib64",
                        "/etc/ld.so.cache", "/etc/localtime", "/etc/hosts", "/etc/nsswitch.conf",
                        "/dev/null", "/dev/urandom", "/dev/random", self.cli,
                        str(Path(self.cli).with_name("codex-code-mode-host"))]
        config = self.runtime / "isolation.json"
        config.write_text(json.dumps({"runtime": str(self.runtime), "work": str(self.work),
            "cli": self.cli, "read_paths": [p for p in system_paths if Path(p).exists()],
            "write_paths": [str(self.runtime), str(self.work), "/dev/null"], "ports": [port]}))
        config.chmod(0o600)
        self.launcher.write_text("#!/bin/sh\nexec /usr/bin/python3 " + shlex.quote(str(program)) + " " + shlex.quote(str(config)) + ' "$@"\n')
        self.launcher.chmod(0o700)


def _context_capacity_response() -> web.Response:
    return web.json_response({"error": {"type": "invalid_request_error",
        "code": "context_length_exceeded", "message": "Complete chat context exceeds request capacity."}}, status=400)


def write_owner_marker(directory: Path) -> None:
    # This sidecar lives outside the child-writable turn directory.
    owner_marker(directory).write_text(json.dumps({"pid": os.getpid(), "start": _process_start(os.getpid())}))


def owner_marker(directory: Path) -> Path:
    return directory.parent / (".aiqq-owner-" + directory.name + ".json")


def cleanup_orphan_turns(root: Path) -> None:
    """Delete only owned turns whose exact creating process is no longer alive."""
    if not root.is_dir():
        return
    for path in root.glob("turn-*"):
        if path.is_symlink() or not path.is_dir():
            continue
        try:
            owner = json.loads(owner_marker(path).read_text())
            if _process_start(int(owner["pid"])) != owner["start"]:
                shutil.rmtree(path)
                owner_marker(path).unlink(missing_ok=True)
        except (OSError, ValueError, KeyError, TypeError):
            continue


def _process_start(pid: int) -> str:
    try:
        # The comm field can contain spaces or parentheses.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except OSError:
        return ""
