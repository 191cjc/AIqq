"""GPT image tool transport using the genuine, isolated Codex SDK runtime."""

from __future__ import annotations

import asyncio
import codecs
import hmac
import json
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from asyncio import sleep as _retry_sleep

import httpx
from aiohttp import web

from aiqq.logic.models import ImageAction, ImageAsset
from aiqq.services.ai.codex_sdk import CodexSDKBackend
from aiqq.services.images.gpt import (
    BAD_GATEWAY_RETRY_DELAYS,
    IMAGE_QUALITY,
    IMAGE_SIZE,
    MAX_ERROR_BODY_BYTES,
    OUTPUT_COMPRESSION,
    SAFE_IMAGE_INSTRUCTION,
    GPTImageError,
    _decode_and_normalize,
    _error_diagnostics,
    _log_event,
    _request_id,
    _safe_endpoint,
    _safe_label,
    _status_kind,
)
from aiqq.services.images.validation import MAX_IMAGE_BASE64_CHARS, validate_image


MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_EVENT_CHARS = 32 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_IMAGE_BASE64_CHARS + 1024 * 1024
ACKNOWLEDGEMENT_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}
HOP_HEADERS = {
    "host", "content-length", "connection", "transfer-encoding", "te",
    "trailer", "upgrade", "proxy-authorization", "proxy-connection",
    "authorization", "content-encoding", "accept-encoding",
}


def _invalid_response() -> GPTImageError:
    return GPTImageError("GPT image response is unusable", kind="invalid_response")


class _ImageEventParser:
    """Bounded SSE framing and one owner for the Responses image result."""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._buffer = ""
        self._data: list[str] = []
        self._event_chars = 0
        self._bytes = 0
        self.completed = False
        self.encoded: str | None = None
        self._image_id: str | None = None

    def feed(self, chunk: bytes, *, final: bool = False) -> None:
        self._bytes += len(chunk)
        if self._bytes > MAX_RESPONSE_BYTES:
            raise _invalid_response()
        try:
            self._buffer += self._decoder.decode(chunk, final=final)
        except UnicodeError as exc:
            raise _invalid_response() from exc
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._line(line.removesuffix("\r"))
        if len(self._buffer) + self._event_chars > MAX_EVENT_CHARS:
            raise _invalid_response()
        if final:
            # A final event need not have a trailing blank line.
            if self._buffer:
                self._line(self._buffer.removesuffix("\r"))
                self._buffer = ""
            self._dispatch()
            if not self.completed or self.encoded is None:
                raise _invalid_response()

    def _line(self, line: str) -> None:
        if not line:
            self._dispatch()
        elif line.startswith("data:"):
            value = line[5:]
            if value.startswith(" "):
                value = value[1:]
            self._event_chars += len(value)
            if self._event_chars > MAX_EVENT_CHARS:
                raise _invalid_response()
            self._data.append(value)

    def _dispatch(self) -> None:
        data = "\n".join(self._data)
        self._data.clear()
        self._event_chars = 0
        if not data or data.strip() == "[DONE]":
            return
        try:
            event = json.loads(data)
        except (ValueError, RecursionError) as exc:
            raise _invalid_response() from exc
        if not isinstance(event, dict):
            raise _invalid_response()
        kind = event.get("type")
        if not isinstance(kind, str):
            raise _invalid_response()
        if kind in {"error", "response.failed", "response.incomplete"}:
            raise _invalid_response()
        if kind == "response.output_item.done":
            self._image(event.get("item"))
        elif kind == "response.completed":
            response = event.get("response")
            if (
                not isinstance(response, dict)
                or response.get("status") != "completed"
                or response.get("error")
                or not isinstance(response.get("output"), list)
            ):
                raise _invalid_response()
            for item in response["output"]:
                self._image(item)
            self.completed = True

    def _image(self, item: object) -> None:
        if not isinstance(item, dict) or item.get("type") != "image_generation_call":
            return
        encoded = item.get("result")
        if (
            item.get("status") != "completed"
            or not isinstance(encoded, str)
            or not encoded
            or len(encoded) > MAX_IMAGE_BASE64_CHARS
        ):
            raise _invalid_response()
        image_id = item.get("id")
        if self.encoded is not None and (
            encoded != self.encoded or image_id != self._image_id
        ):
            # One business request may only deliver one completed image call.
            raise _invalid_response()
        self.encoded = encoded
        self._image_id = image_id if isinstance(image_id, str) else None


@dataclass
class _ImageRun:
    result: asyncio.Future[ImageAsset | GPTImageError]
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    accepted: bool = False
    status: int | None = None
    provider_request_id: str | None = None
    diagnostics: dict[str, object] = field(default_factory=dict)
    handlers: set[asyncio.Task] = field(default_factory=set)

    def finish(self, result: ImageAsset | GPTImageError) -> None:
        if not self.result.done():
            self.result.set_result(result)


class CodexResponsesImageService:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str = "gpt-image-2",
        driver_model: str = "gpt-6-astra",
        timeout_seconds: float = 240,
        cli_path: str = "",
        runtime_dir: str | Path = "/var/lib/aiqq/codex-runtime",
        work_dir: str | Path = "/var/lib/aiqq/codex-work",
        process_environment: Mapping[str, str] | None = None,
        backend_factory: Callable[..., Any] = CodexSDKBackend,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
        ):
            raise ValueError("image base URL must be an HTTP endpoint without credentials")
        if not api_key.strip() or not model.strip() or not driver_model.strip():
            raise ValueError("image API key and models must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("image timeout must be positive")
        self.model = model
        self.driver_model = driver_model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = float(timeout_seconds)
        self._backend_factory = backend_factory
        self._backend_options = {
            "cli_path": cli_path,
            "runtime_dir": runtime_dir,
            "work_dir": work_dir,
            "process_environment": dict(process_environment or {}),
        }
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout, connect=min(15, self._timeout)),
            follow_redirects=False,
            trust_env=False,
        )
        self._active: set[asyncio.Task] = set()
        self._closing = False

    async def generate(
        self, action: ImageAction, *, reference_image: ImageAsset | None = None
    ) -> ImageAsset:
        attempt_id = uuid4().hex
        operation = "edit" if action.mode == "edit" else "generate"
        secret_values = (self._api_key, action.prompt)
        fields = {
            "attempt_id": attempt_id,
            "operation": operation,
            "model": _safe_label(self.model, secret_values),
            "driver_model": _safe_label(self.driver_model, secret_values),
            "endpoint": _safe_endpoint(self._base_url, "/responses", secret_values),
        }
        started = monotonic()
        state = _ImageRun(asyncio.get_running_loop().create_future())
        task = asyncio.current_task()
        self._active.add(task)
        outcome, kind = "failed", "unknown"
        _log_event("gpt_image_started", fields)
        try:
            if self._closing:
                raise GPTImageError("GPT image service is closed")
            if operation == "edit":
                if reference_image is None:
                    raise GPTImageError("reference image is required", kind="invalid_request")
                try:
                    reference_image = validate_image(reference_image.data)
                except ValueError as exc:
                    raise GPTImageError("reference image is invalid", kind="invalid_request") from exc
            else:
                reference_image = None
            async with asyncio.timeout(self._timeout):
                image = await self._run(
                    state, action, reference_image, fields, secret_values, started
                )
            outcome, kind = "success", "none"
            return image
        except asyncio.CancelledError:
            outcome, kind = "cancelled", "cancelled"
            raise
        except Exception as exc:
            if isinstance(exc, GPTImageError):
                kind = exc.kind
            elif isinstance(exc, (TimeoutError, httpx.TimeoutException)):
                kind = "timeout"
            elif isinstance(exc, httpx.RequestError):
                kind = "connection"
            raise GPTImageError(
                "GPT image service is unavailable",
                kind=kind,
                attempt_id=attempt_id,
                status_code=state.status,
                provider_request_id=state.provider_request_id,
            ) from exc
        finally:
            self._active.discard(task)
            _log_event(
                "gpt_image_finished",
                {
                    **fields,
                    "elapsed_ms": round((monotonic() - started) * 1000),
                    "outcome": outcome,
                    "status": state.status,
                    "error_kind": kind,
                    "provider_request_id": state.provider_request_id,
                    **state.diagnostics,
                },
                failed=outcome == "failed",
            )

    async def _run(
        self, state, action, reference_image, fields, secret_values, started
    ) -> ImageAsset:
        async def handle(request: web.Request) -> web.Response:
            authorization = request.headers.get("Authorization", "")
            if not authorization.isascii() or not hmac.compare_digest(
                authorization, f"Bearer {state.token}"
            ):
                return _local_error(401)
            if request.query_string or state.accepted:
                return _local_error(400)
            state.accepted = True
            handler = asyncio.current_task()
            state.handlers.add(handler)
            try:
                body = await request.json()
                if not isinstance(body, dict) or body.get("model") != self.driver_model:
                    raise GPTImageError("unexpected image driver model", kind="invalid_request")
                tool = {
                    "type": "image_generation", "model": self.model,
                    "size": IMAGE_SIZE, "quality": IMAGE_QUALITY,
                    "output_format": "jpeg", "output_compression": OUTPUT_COMPRESSION,
                }
                if action.mode == "edit":
                    tool["input_fidelity"] = "high"
                body.update(tools=[tool], tool_choice={"type": "image_generation"}, stream=True)
                blocked = HOP_HEADERS | {
                    name.strip().lower()
                    for name in request.headers.get("Connection", "").split(",")
                }
                headers = {
                    name: value for name, value in request.headers.items()
                    if name.lower() not in blocked
                }
                headers.update({
                    "Authorization": f"Bearer {self._api_key}",
                    "Accept-Encoding": "identity", "Content-Type": "application/json",
                })
                image, stream = await self._forward(
                    state, body, headers, fields, secret_values, started
                )
                state.finish(image)
                return web.Response(body=stream, content_type="text/event-stream")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                kind = "unknown"
                if isinstance(exc, GPTImageError):
                    kind = exc.kind
                elif isinstance(exc, httpx.TimeoutException):
                    kind = "timeout"
                elif isinstance(exc, httpx.RequestError):
                    kind = "connection"
                elif isinstance(exc, (ValueError, web.HTTPRequestEntityTooLarge)):
                    kind = "invalid_request"
                # Return fixed local prose, never echo upstream bodies into CLI output.
                state.finish(GPTImageError("GPT image transport failed", kind=kind))
                return _local_error(400)
            finally:
                state.handlers.discard(handler)

        app = web.Application(client_max_size=MAX_REQUEST_BYTES)
        app.router.add_post("/v1/responses", handle)
        runner = web.AppRunner(app, access_log=None, shutdown_timeout=2)
        backend = None
        sdk_task = None
        try:
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            environment = dict(self._backend_options["process_environment"])
            # The genuine CLI must reach the loopback bridge directly.
            environment["NO_PROXY"] = ",".join(filter(None, (
                environment.get("NO_PROXY", ""), "127.0.0.1", "localhost",
            )))
            backend = self._backend_factory(
                **{**self._backend_options, "process_environment": environment},
                api_key=state.token,
                base_url=f"http://127.0.0.1:{port}/v1",
                model=self.driver_model,
                turn_timeout_seconds=self._timeout,
                image_generation_mode=True,
                max_concurrent=1,
            )
            sdk_task = asyncio.create_task(backend.run(
                instructions="Generate exactly one requested image, then acknowledge completion.",
                model_input=SAFE_IMAGE_INSTRUCTION + action.prompt,
                output_schema=ACKNOWLEDGEMENT_SCHEMA,
                input_images=(reference_image,) if reference_image is not None else (),
            ))
            await asyncio.wait({sdk_task, state.result}, return_when=asyncio.FIRST_COMPLETED)
            if not state.result.done() and not state.accepted:
                # No upstream response exists when the CLI fails before its request.
                await sdk_task
                raise _invalid_response()
            result = await state.result
            if isinstance(result, GPTImageError):
                raise result
            return result
        finally:
            # Cancel the genuine runtime before removing the bridge or its input files.
            if sdk_task is not None:
                sdk_task.cancel()
                await asyncio.gather(sdk_task, return_exceptions=True)
            if backend is not None:
                await backend.close()
            handlers = tuple(state.handlers)
            for handler in handlers:
                handler.cancel()
            if handlers:
                await asyncio.gather(*handlers, return_exceptions=True)
            await runner.cleanup()
            state.result.cancel()

    async def _forward(self, state, body, headers, fields, secret_values, started):
        for retry_index in range(len(BAD_GATEWAY_RETRY_DELAYS) + 1):
            state.status, state.provider_request_id = None, None
            state.diagnostics = {}
            async with self._client.stream(
                "POST", self._base_url + "/responses", headers=headers, json=body,
                follow_redirects=False,
            ) as response:
                state.status = response.status_code
                state.provider_request_id = _request_id(response.headers, secret_values)
                if response.status_code != 200:
                    state.diagnostics = await _read_error(response, self.model, secret_values)
                    if response.status_code != 502 or retry_index == len(BAD_GATEWAY_RETRY_DELAYS):
                        raise GPTImageError("GPT image HTTP request failed", kind=_status_kind(response.status_code))
                    delay = BAD_GATEWAY_RETRY_DELAYS[retry_index]
                    _log_event("gpt_image_retry", {
                        **fields,
                        "elapsed_ms": round((monotonic() - started) * 1000),
                        "status": state.status, "error_kind": "upstream",
                        "provider_request_id": state.provider_request_id,
                        "retry_number": retry_index + 1, "delay_seconds": delay,
                        **state.diagnostics,
                    })
                else:
                    if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "text/event-stream":
                        raise _invalid_response()
                    parser = _ImageEventParser()
                    chunks = []
                    # Buffer a bounded SSE response until its terminal event. This
                    # prevents CLI follow-up requests or unsupported local event
                    # decoding from interrupting an already-running image tool.
                    async for chunk in response.aiter_bytes():
                        parser.feed(chunk)
                        chunks.append(chunk)
                    parser.feed(b"", final=True)
                    image = await asyncio.to_thread(_decode_and_normalize, SimpleNamespace(
                        data=[SimpleNamespace(b64_json=parser.encoded)]
                    ))
                    return image, b"".join(chunks)
            await _retry_sleep(delay)
        raise _invalid_response()

    async def close(self) -> None:
        self._closing = True
        active = tuple(task for task in self._active if task is not asyncio.current_task())
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        if self._owns_client:
            await self._client.aclose()


def _local_error(status: int) -> web.Response:
    return web.json_response({"error": {
        "type": "invalid_request_error", "message": "Image transport request unavailable",
    }}, status=status)


async def _read_error(response, model, secret_values) -> dict[str, object]:
    data = bytearray()
    async for chunk in response.aiter_bytes():
        data.extend(chunk[:MAX_ERROR_BODY_BYTES + 1 - len(data)])
        if len(data) > MAX_ERROR_BODY_BYTES:
            break
    # aiter_bytes already decoded Content-Encoding; retaining it would decode
    # compressed errors a second time and incorrectly suppress a 502 retry.
    bounded = httpx.Response(
        response.status_code,
        headers={"content-type": response.headers.get("content-type", "")},
        content=bytes(data),
    )
    return _error_diagnostics(bounded, model, secret_values)
