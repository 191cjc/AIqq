import asyncio
import base64
import gzip
import json
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

import httpx
from PIL import Image

from aiqq.logic.models import ImageAction
from aiqq.services.images.codex_responses import CodexResponsesImageService
from aiqq.services.images.gpt import GPTImageError, SAFE_IMAGE_INSTRUCTION
from aiqq.services.images.validation import validate_image


API_KEY = "sk-private-image-key"
PROMPT = "private prompt 青色圆圈"
LOGGER = "aiqq.services.images.gpt"


def png_bytes():
    output = BytesIO()
    Image.new("RGBA", (24, 12), (0, 0, 255, 128)).save(output, format="PNG")
    return output.getvalue()


def image_item(**overrides):
    return {
        "type": "image_generation_call", "id": "ig-test", "status": "completed",
        "result": base64.b64encode(png_bytes()).decode(), **overrides,
    }


def completed_event(output=None, **overrides):
    return {"type": "response.completed", "response": {
        "status": "completed", "output": [image_item()] if output is None else output,
        **overrides,
    }}


def sse(*events):
    return b"".join(
        ("data: " + json.dumps(event, ensure_ascii=False) + "\r\n\r\n").encode()
        for event in events
    )


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, wait=None, fail=None):
        self.chunks = chunks
        self.wait = wait
        self.fail = fail
        self.closed = False
        self.started = asyncio.Event()

    async def __aiter__(self):
        self.started.set()
        for chunk in self.chunks:
            yield chunk
        if self.wait is not None:
            await self.wait.wait()
        if self.fail is not None:
            raise self.fail

    async def aclose(self):
        self.closed = True


def response(events=None, stream=None):
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream", "x-request-id": "req-success"},
        stream=stream or ChunkStream([sse(*(events or [completed_event()]))]),
    )


class FakeBackend:
    """Exercise the actual loopback boundary; only CLI launch is replaced."""

    def __init__(self, owner, options):
        self.owner = owner
        self.options = options
        self.closed = False
        self.cancelled = False
        self.run_options = None
        self.payload = None

    async def run(self, **options):
        self.run_options = options
        content = [{"type": "input_text", "text": options["model_input"]}]
        for image in options["input_images"]:
            content.append({
                "type": "input_image",
                "image_url": "data:" + image.mime_type + ";base64," + base64.b64encode(image.data).decode(),
            })
        self.payload = {
            "model": self.options["model"],
            "input": [{"role": "user", "content": content}],
            "tools": [{"type": "web_search"}], "stream": True,
        }
        headers = {
            "Authorization": "Bearer " + self.options["api_key"],
            "User-Agent": "test-actual-client/1.0", "originator": "test-actual-client",
            "x-client-preserved": "yes",
        }
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
                if self.owner.plan:
                    await self.owner.plan(self, client, headers)
                else:
                    await client.post(self.options["base_url"] + "/responses", headers=headers, json=self.payload)
            return SimpleNamespace(text='{"ok":true}')
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def close(self):
        self.closed = True


class FakeSDK:
    def __init__(self, plan=None):
        self.backends = []
        self.plan = plan

    def factory(self, **options):
        backend = FakeBackend(self, options)
        self.backends.append(backend)
        return backend


class CodexResponsesImageTests(unittest.IsolatedAsyncioTestCase):
    def service(self, handler, *, plan=None, **overrides):
        requests = []

        async def transport(request):
            requests.append(request)
            result = handler(request)
            return await result if hasattr(result, "__await__") else result

        client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        self.addAsyncCleanup(client.aclose)
        sdk = FakeSDK(plan)
        service = CodexResponsesImageService(
            api_key=API_KEY, base_url="https://images.example/v1",
            backend_factory=sdk.factory, http_client=client, **overrides,
        )
        self.addAsyncCleanup(service.close)
        return service, sdk, requests

    async def assert_cleaned(self, service, sdk):
        self.assertFalse(service._active)
        self.assertTrue(all(backend.closed for backend in sdk.backends))
        if sdk.backends:
            async with httpx.AsyncClient(trust_env=False) as client:
                with self.assertRaises(httpx.ConnectError):
                    await client.post(sdk.backends[-1].options["base_url"] + "/responses")

    async def test_generate_and_edit_preserve_sdk_input_and_authenticate_upstream(self):
        for mode in ("generate", "edit"):
            with self.subTest(mode=mode):
                service, sdk, requests = self.service(lambda _: response())
                action = ImageAction(mode, PROMPT, 1 if mode == "edit" else None)
                reference = validate_image(png_bytes()) if mode == "edit" else None
                with self.assertLogs(LOGGER, level="INFO") as logs:
                    image = await service.generate(action, reference_image=reference)
                self.assertEqual((image.mime_type, image.width, image.height), ("image/jpeg", 1024, 1024))
                with Image.open(BytesIO(image.data)) as actual:
                    self.assertEqual(actual.format, "JPEG")
                self.assertEqual(len(requests), 1)
                request = requests[0]
                self.assertEqual(str(request.url), "https://images.example/v1/responses")
                self.assertEqual(request.headers["authorization"], "Bearer " + API_KEY)
                self.assertEqual(request.headers["originator"], "test-actual-client")
                self.assertEqual(request.headers["user-agent"], "test-actual-client/1.0")
                self.assertEqual(request.headers["x-client-preserved"], "yes")
                body = json.loads(request.content)
                self.assertEqual(body["model"], "gpt-6-astra")
                self.assertEqual(body["input"], sdk.backends[0].payload["input"])
                self.assertEqual(body["input"][0]["content"][0]["text"], SAFE_IMAGE_INSTRUCTION + PROMPT)
                tool = body["tools"][0]
                self.assertEqual(tool["type"], "image_generation")
                self.assertEqual(tool["model"], "gpt-image-2")
                self.assertEqual(tool["size"], "1024x1024")
                self.assertEqual(tool["quality"], "medium")
                self.assertEqual(tool["output_compression"], 85)
                self.assertEqual(tool["output_format"], "jpeg")
                self.assertEqual(tool.get("input_fidelity"), "high" if reference else None)
                self.assertEqual(body["tool_choice"], {"type": "image_generation"})
                self.assertEqual(len(body["tools"]), 1)
                backend_options = sdk.backends[0].options
                self.assertTrue(backend_options["image_generation_mode"])
                self.assertNotEqual(backend_options["api_key"], API_KEY)
                self.assertNotIn(backend_options["api_key"], request.content.decode())
                self.assertIn("127.0.0.1", backend_options["process_environment"]["NO_PROXY"])
                self.assertNotIn(PROMPT, " ".join(logs.output))
                self.assertNotIn(API_KEY, " ".join(logs.output))
                events = [json.loads(record.getMessage().split(" ", 1)[1]) for record in logs.records]
                self.assertEqual(events[-1]["outcome"], "success")
                self.assertEqual(events[0]["attempt_id"], events[-1]["attempt_id"])
                await self.assert_cleaned(service, sdk)

    async def test_tokens_are_fresh_for_each_business_run(self):
        service, sdk, requests = self.service(lambda _: response())
        for _ in range(2):
            await service.generate(ImageAction("generate", PROMPT))
        self.assertNotEqual(sdk.backends[0].options["api_key"], sdk.backends[1].options["api_key"])
        self.assertEqual(len(requests), 2)

    async def test_sse_handles_arbitrary_utf8_and_event_boundaries(self):
        item = image_item()
        payload = sse(
            {"type": "response.output_text.delta", "delta": "你好"},
            {"type": "response.output_item.done", "item": item},
            completed_event([item]),
        ) + b"data: [DONE]\r\n\r\n"
        stream = ChunkStream([payload[index:index + 1] for index in range(len(payload))])
        service, sdk, _ = self.service(lambda _: response(stream=stream))
        image = await service.generate(ImageAction("generate", PROMPT))
        self.assertEqual(image.width, 1024)
        self.assertTrue(stream.closed)
        await self.assert_cleaned(service, sdk)

    async def test_missing_failed_incomplete_refused_and_invalid_images_fail(self):
        cases = [
            [{"type": "response.output_item.done", "item": image_item()}],
            [{"type": "response.failed", "response": {"error": {"message": API_KEY + PROMPT}}}],
            [{"type": "response.incomplete", "response": {}}],
            [{"type": "error", "message": API_KEY + PROMPT}],
            [completed_event([])],
            [completed_event([{"type": "message", "content": [{"type": "refusal", "refusal": PROMPT}]}])],
            [completed_event([image_item(status="in_progress")])],
            [completed_event([image_item(result="bad base64!")])],
            [completed_event([image_item(result=base64.b64encode(b"not an image").decode())])],
            [completed_event([image_item(), image_item(id="another-call")])],
            [completed_event(status="incomplete")],
        ]
        for index, events in enumerate(cases):
            with self.subTest(case=index):
                service, sdk, requests = self.service(lambda _: response(events))
                with self.assertLogs(LOGGER, level="INFO") as logs:
                    with self.assertRaises(GPTImageError) as caught:
                        await service.generate(ImageAction("generate", PROMPT))
                self.assertEqual(caught.exception.kind, "invalid_response")
                self.assertEqual(len(requests), 1)
                self.assertNotIn(PROMPT, " ".join(logs.output))
                self.assertNotIn(API_KEY, " ".join(logs.output))
                await self.assert_cleaned(service, sdk)

    async def test_invalid_framing_or_non_sse_200_never_retries(self):
        for payload in (
            b"data: {not-json}\n\n", b"data: \xff\n\n", b"data: []\n\n",
            sse({"type": []}), sse({"type": {}}), sse({}),
        ):
            with self.subTest(payload=payload):
                service, _, requests = self.service(lambda _: response(stream=ChunkStream([payload])))
                with self.assertRaises(GPTImageError) as caught:
                    await service.generate(ImageAction("generate", PROMPT))
                self.assertEqual(caught.exception.kind, "invalid_response")
                self.assertEqual(len(requests), 1)
        service, _, requests = self.service(lambda _: httpx.Response(200, json={"ok": True}))
        with self.assertRaises(GPTImageError):
            await service.generate(ImageAction("generate", PROMPT))
        self.assertEqual(len(requests), 1)

    async def test_only_502_retries_with_unchanged_edit_or_generation_body(self):
        for mode in ("generate", "edit"):
            for failures in (1, 2, 3):
                with self.subTest(mode=mode, failures=failures):
                    remaining = failures

                    def handler(_):
                        nonlocal remaining
                        if remaining:
                            remaining -= 1
                            return httpx.Response(502, json={"error": {"message": "Internal server error"}})
                        return response()

                    service, sdk, requests = self.service(handler)
                    action = ImageAction(mode, PROMPT, 1 if mode == "edit" else None)
                    with patch("aiqq.services.images.codex_responses.BAD_GATEWAY_RETRY_DELAYS", (0, 0)):
                        if failures == 3:
                            with self.assertRaises(GPTImageError) as caught:
                                await service.generate(action, reference_image=validate_image(png_bytes()))
                            self.assertEqual(caught.exception.kind, "upstream")
                            self.assertEqual(caught.exception.status_code, 502)
                        else:
                            await service.generate(action, reference_image=validate_image(png_bytes()))
                    self.assertEqual(len(requests), min(failures + 1, 3))
                    self.assertEqual(len({request.content for request in requests}), 1)
                    self.assertEqual(len(sdk.backends), 1)
                    await self.assert_cleaned(service, sdk)

    async def test_non_502_statuses_never_retry_and_details_are_safe(self):
        statuses = {400: "invalid_request", 401: "authentication", 403: "authentication", 404: "not_found", 429: "rate_limited", 500: "upstream", 503: "upstream", 504: "upstream"}
        for status, kind in statuses.items():
            with self.subTest(status=status):
                service, sdk, requests = self.service(lambda _: httpx.Response(status, json={"error": {
                    "type": "model_not_found", "message": API_KEY + PROMPT,
                }}, headers={"x-request-id": "req-error"}))
                with self.assertLogs(LOGGER, level="INFO") as logs:
                    with self.assertRaises(GPTImageError) as caught:
                        await service.generate(ImageAction("generate", PROMPT))
                self.assertEqual(caught.exception.kind, kind)
                self.assertEqual(caught.exception.status_code, status)
                self.assertEqual(caught.exception.provider_request_id, "req-error")
                self.assertEqual(len(requests), 1)
                self.assertNotIn(API_KEY, " ".join(logs.output))
                self.assertNotIn(PROMPT, " ".join(logs.output))
                self.assertNotIn(API_KEY, str(caught.exception))
                await self.assert_cleaned(service, sdk)

    async def test_compressed_502_errors_keep_safe_diagnostics_and_retry_delays(self):
        count = 0

        def handler(_):
            nonlocal count
            count += 1
            if count <= 2:
                return httpx.Response(502, headers={
                    "content-type": "application/json", "content-encoding": "gzip",
                    "x-request-id": f"req-retry-{count}",
                }, content=gzip.compress(json.dumps({"error": {
                    "type": "server_error", "message": API_KEY + PROMPT,
                }}).encode()))
            return response()

        service, sdk, requests = self.service(handler)
        with self.assertLogs(LOGGER, level="INFO") as logs:
            with patch("aiqq.services.images.codex_responses._retry_sleep", new_callable=AsyncMock) as sleep:
                await service.generate(ImageAction("generate", PROMPT))
        self.assertEqual(sleep.await_args_list, [call(0.5), call(1.0)])
        self.assertEqual(len(requests), 3)
        retries = [json.loads(record.getMessage().split(" ", 1)[1]) for record in logs.records if record.getMessage().startswith("event=gpt_image_retry ")]
        self.assertEqual([event["provider_request_id"] for event in retries], ["req-retry-1", "req-retry-2"])
        self.assertEqual(len({event["attempt_id"] for event in retries}), 1)
        self.assertNotIn(API_KEY, " ".join(logs.output))
        self.assertNotIn(PROMPT, " ".join(logs.output))
        await self.assert_cleaned(service, sdk)

    async def test_502_then_timeout_discards_stale_http_metadata(self):
        count = 0

        def handler(request):
            nonlocal count
            count += 1
            if count == 1:
                return httpx.Response(502, headers={"x-request-id": "req-stale"})
            raise httpx.ReadTimeout("do not log " + API_KEY, request=request)

        service, sdk, requests = self.service(handler)
        with patch("aiqq.services.images.codex_responses.BAD_GATEWAY_RETRY_DELAYS", (0, 0)):
            with self.assertRaises(GPTImageError) as caught:
                await service.generate(ImageAction("generate", PROMPT))
        self.assertEqual(caught.exception.kind, "timeout")
        self.assertIsNone(caught.exception.status_code)
        self.assertIsNone(caught.exception.provider_request_id)
        self.assertEqual(len(requests), 2)
        await self.assert_cleaned(service, sdk)

    async def test_outer_timeout_cancellation_and_close_abort_stream_and_sdk(self):
        for finish in ("timeout", "cancel", "close"):
            with self.subTest(finish=finish):
                stream = ChunkStream([sse({"type": "response.created"})], wait=asyncio.Event())
                service, sdk, requests = self.service(lambda _: response(stream=stream), timeout_seconds=0.1 if finish == "timeout" else 10)
                task = asyncio.create_task(service.generate(ImageAction("generate", PROMPT)))
                await asyncio.wait_for(stream.started.wait(), 1)
                if finish == "timeout":
                    with self.assertRaises(GPTImageError) as caught:
                        await task
                    self.assertEqual(caught.exception.kind, "timeout")
                else:
                    if finish == "cancel":
                        task.cancel()
                    else:
                        await service.close()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                self.assertTrue(stream.closed)
                self.assertTrue(sdk.backends[0].cancelled)
                self.assertEqual(len(requests), 1)
                await self.assert_cleaned(service, sdk)

    async def test_cancel_during_502_backoff_never_posts_again(self):
        service, sdk, requests = self.service(lambda _: httpx.Response(502))
        sleep_started = asyncio.Event()

        async def wait(_):
            sleep_started.set()
            await asyncio.Event().wait()

        with patch("aiqq.services.images.codex_responses._retry_sleep", side_effect=wait):
            task = asyncio.create_task(service.generate(ImageAction("generate", PROMPT)))
            await asyncio.wait_for(sleep_started.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(len(requests), 1)
        await self.assert_cleaned(service, sdk)

    async def test_cancellation_closes_an_accepted_stalled_request_body(self):
        body_started = asyncio.Event()
        socket_closed = asyncio.Event()

        async def plan(backend, _client, _headers):
            port = httpx.URL(backend.options["base_url"]).port
            _reader, writer = await asyncio.open_connection("127.0.0.1", port)
            try:
                request = (
                    "POST /v1/responses HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                    "Content-Type: application/json\r\nContent-Length: 100000\r\n"
                    "Authorization: Bearer " + backend.options["api_key"] + "\r\n\r\n{"
                )
                writer.write(request.encode())
                await writer.drain()
                # Let the server enter its bounded request.json read.
                await asyncio.sleep(0.02)
                body_started.set()
                await asyncio.Event().wait()
            finally:
                writer.close()
                await writer.wait_closed()
                socket_closed.set()

        service, sdk, requests = self.service(lambda _: response(), plan=plan)
        task = asyncio.create_task(service.generate(ImageAction("generate", PROMPT)))
        await asyncio.wait_for(body_started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        self.assertTrue(socket_closed.is_set())
        self.assertEqual(requests, [])
        await self.assert_cleaned(service, sdk)

    async def test_loopback_rejects_unauthenticated_requests_and_multiple_posts(self):
        upstream_started = asyncio.Event()
        release = asyncio.Event()
        observed = []

        async def handler(_):
            upstream_started.set()
            await release.wait()
            return response()

        async def plan(backend, client, headers):
            url = backend.options["base_url"] + "/responses"
            denied = await client.post(url, json=backend.payload)
            observed.append(denied.status_code)
            denied = await client.post(url, headers={b"Authorization": b"Bearer \xc3\xa9"}, json=backend.payload)
            observed.append(denied.status_code)
            denied = await client.post(url + "/extra", headers=headers, json=backend.payload)
            observed.append(denied.status_code)
            denied = await client.post(url + "?target=other", headers=headers, json=backend.payload)
            observed.append(denied.status_code)
            first = asyncio.create_task(client.post(url, headers=headers, json=backend.payload))
            try:
                await upstream_started.wait()
                denied = await client.post(url, headers=headers, json=backend.payload)
                observed.append(denied.status_code)
                release.set()
                await first
            finally:
                first.cancel()
                await asyncio.gather(first, return_exceptions=True)

        service, sdk, requests = self.service(handler, plan=plan)
        await service.generate(ImageAction("generate", PROMPT))
        self.assertEqual(observed, [401, 401, 404, 400, 400])
        self.assertEqual(len(requests), 1)
        await self.assert_cleaned(service, sdk)

    async def test_unexpected_driver_and_missing_edit_input_do_not_reach_upstream(self):
        async def plan(backend, client, headers):
            backend.payload["model"] = "unapproved-model"
            await client.post(backend.options["base_url"] + "/responses", headers=headers, json=backend.payload)

        service, sdk, requests = self.service(lambda _: response(), plan=plan)
        with self.assertRaises(GPTImageError) as caught:
            await service.generate(ImageAction("generate", PROMPT))
        self.assertEqual(caught.exception.kind, "invalid_request")
        self.assertEqual(requests, [])
        await self.assert_cleaned(service, sdk)
        with self.assertRaises(GPTImageError) as caught:
            await service.generate(ImageAction("edit", PROMPT, 1))
        self.assertEqual(caught.exception.kind, "invalid_request")
        self.assertEqual(len(sdk.backends), 1)

    async def test_stream_truncation_and_limits_close_without_retry(self):
        for failure in ("connection", "limit"):
            with self.subTest(failure=failure):
                stream = ChunkStream([sse({"type": "response.created"})], fail=httpx.ReadError("private failure") if failure == "connection" else None)
                service, sdk, requests = self.service(lambda _: response(stream=stream))
                with patch("aiqq.services.images.codex_responses.MAX_RESPONSE_BYTES", 10 if failure == "limit" else 1024):
                    with self.assertRaises(GPTImageError):
                        await service.generate(ImageAction("generate", PROMPT))
                self.assertEqual(len(requests), 1)
                self.assertTrue(stream.closed)
                await self.assert_cleaned(service, sdk)

    async def test_backend_failure_before_request_is_safe_and_cleans_up(self):
        async def plan(*_):
            raise RuntimeError(API_KEY + PROMPT)

        service, sdk, requests = self.service(lambda _: response(), plan=plan)
        with self.assertLogs(LOGGER, level="INFO") as logs:
            with self.assertRaises(GPTImageError):
                await service.generate(ImageAction("generate", PROMPT))
        self.assertEqual(requests, [])
        self.assertNotIn(API_KEY, " ".join(logs.output))
        await self.assert_cleaned(service, sdk)


if __name__ == "__main__":
    unittest.main()
