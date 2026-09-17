"""Standalone native web requests cross only the authenticated chat bridge."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import web
import httpx

from aiqq.services.ai.chat_runtime import ChatRuntime
from aiqq.services.ai.codex_sdk import CodexSDKBackend, find_codex_cli

LOGGER = "aiqq.services.ai.chat_runtime"
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}},
          "required": ["ok"], "additionalProperties": False}


class ReadSession:
    async def read_history(self, **filters):
        return {"messages": [], "has_more": False}

    async def read_image(self, **filters):
        return {"status": "error", "message": "unused"}, ()


class ChatSearchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.upstream_calls = []
        self.bridge_number = 0
        self.search_status = 200
        self.search_body = b'{"output":"synthetic result","results":[]}'
        self.search_headers = {"Content-Type": "application/json", "X-Native-Result": "retained"}
        self.custom_handler = None

        async def endpoint(request):
            body = await request.read()
            self.upstream_calls.append((request.path, body, dict(request.headers)))
            if self.custom_handler:
                return await self.custom_handler(request, body)
            return web.Response(status=self.search_status, body=self.search_body, headers=self.search_headers)

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", endpoint)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        self.addAsyncCleanup(self.runner.cleanup)
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.base_url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/custom/v1"
        self.client = httpx.AsyncClient(trust_env=False, follow_redirects=False)
        self.addAsyncCleanup(self.client.aclose)

    @asynccontextmanager
    async def bridge(self, **options):
        self.bridge_number += 1
        root = Path(self.folder.name, str(self.bridge_number))
        work, runtime = root / "work", root / "runtime"
        work.mkdir(parents=True)
        runtime.mkdir()
        async with ChatRuntime(session=ReadSession(), work=work, runtime=runtime,
                               cli="/usr/bin/true", api_key="provider-secret",
                               base_url=self.base_url + "/", model="configured-model", **options) as bridge:
            yield bridge

    async def post(self, bridge, *, headers=None, **kwargs):
        return await self.client.post(bridge.url + "/v1/alpha/search",
            headers=headers or {"Authorization": "Bearer " + bridge.model_token}, **kwargs)

    async def test_authentication_default_disabled_and_exact_route(self):
        async with self.bridge() as bridge:
            for headers in ({"Authorization": "Bearer wrong"},
                            {"Authorization": "Bearer " + bridge.read_token}):
                response = await self.post(bridge, headers=headers, json={})
                self.assertEqual(response.status_code, 401)
            response = await self.post(bridge, json={})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["error"]["code"], "disabled")
            self.assertEqual((await self.client.get(bridge.url + "/v1/alpha/search")).status_code, 405)
            self.assertEqual((await self.client.post(bridge.url + "/v1/alpha/other", json={})).status_code, 404)
        self.assertEqual(self.upstream_calls, [])

    async def test_native_bytes_headers_and_credentials_are_forwarded_without_history_mutation(self):
        body = '{ "model": "native-model", "commands": {"search_query": [{"q": "测试"}]}, "input": [], "unknown": null }'.encode()
        async with self.bridge(enable_web_search=True) as bridge:
            bridge._history_context.append({"private": "history must not enter search"})
            headers = {"Authorization": "Bearer " + bridge.model_token,
                "Content-Type": "application/json", "User-Agent": "codex_cli_rs/0.152.1",
                "Originator": "codex_cli_rs", "X-Native-Feature": "unchanged"}
            with self.assertLogs(LOGGER, level="INFO") as logs:
                response = await self.post(bridge, content=body, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, self.search_body)
            self.assertEqual(response.headers["X-Native-Result"], "retained")
            path, sent, actual = self.upstream_calls[0]
            self.assertEqual(path, "/custom/v1/alpha/search")
            self.assertEqual(sent, body)
            self.assertEqual(actual["Authorization"], "Bearer provider-secret")
            for name in ("User-Agent", "Originator", "X-Native-Feature", "Content-Type"):
                self.assertEqual(actual[name], headers[name])
            self.assertNotIn(bridge.model_token, json.dumps(actual))
            self.assertIn("reason=success", "\n".join(logs.output))

    async def test_bad_json_and_request_sizes_include_chunked_input(self):
        async with self.bridge(enable_web_search=True) as bridge:
            for invalid in (b"not json", b"[]", b"null", b"\xff"):
                self.assertEqual((await self.post(bridge, content=invalid)).status_code, 400)
            with patch("aiqq.services.ai.chat_runtime.MAX_SEARCH_REQUEST_BYTES", 32):
                self.assertEqual((await self.post(bridge, content=b"x" * 33)).status_code, 413)
                async def chunks():
                    yield b'{"q":"'
                    yield b"x" * 33
                    yield b'"}'
                self.assertEqual((await self.post(bridge, content=chunks())).status_code, 413)
        self.assertEqual(self.upstream_calls, [])

    async def test_request_budget_and_response_limit(self):
        async with self.bridge(enable_web_search=True) as bridge:
            with patch("aiqq.services.ai.chat_runtime.MAX_SEARCH_REQUESTS", 2):
                for expected in (200, 200, 429):
                    self.assertEqual((await self.post(bridge, json={})).status_code, expected)
            self.assertEqual(len(self.upstream_calls), 2)
        async with self.bridge(enable_web_search=True) as bridge:
            with patch("aiqq.services.ai.chat_runtime.MAX_SEARCH_RESPONSE_BYTES", 8):
                response = await self.post(bridge, json={})
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.json()["error"]["code"], "response_too_large")

    async def test_upstream_http_failures_redirects_and_logs(self):
        sensitive = ["provider-secret", "private-query", "private-body", "private-header", self.base_url]
        async with self.bridge(enable_web_search=True) as bridge:
            sensitive.append(bridge.model_token)
            for status in (301, 401, 403, 429, 502):
                self.search_status = status
                self.search_body = b"private-body"
                self.search_headers["Location"] = self.base_url + "/redirect-never-followed"
                with self.assertLogs(LOGGER, level="WARNING") as logs:
                    response = await self.post(bridge, json={"q": "private-query"}, headers={
                        "Authorization": "Bearer " + bridge.model_token, "X-Private": "private-header"})
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.content, b"private-body")
                text = "\n".join(logs.output)
                self.assertIn(f"status={status}", text)
                self.assertIn("reason=upstream_http_error", text)
                for value in sensitive:
                    self.assertNotIn(value, text)
            self.assertEqual(len(self.upstream_calls), 5)
            self.assertTrue(all(path.endswith("/alpha/search") for path, _, _ in self.upstream_calls))

    async def test_network_timeout_and_cancellation(self):
        async with self.bridge(enable_web_search=True) as bridge:
            await bridge._client.aclose()
            async def fail(request):
                raise httpx.ConnectError("private-query private-key private-url", request=request)
            bridge._client = httpx.AsyncClient(transport=httpx.MockTransport(fail))
            with self.assertLogs(LOGGER, level="WARNING") as logs:
                response = await self.post(bridge, json={})
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.json()["error"]["code"], "upstream_unavailable")
            self.assertNotIn("private-", "\n".join(logs.output))
            await bridge._client.aclose()
            entered, cancelled = asyncio.Event(), asyncio.Event()
            async def hang(request):
                entered.set()
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()
            bridge._client = httpx.AsyncClient(transport=httpx.MockTransport(hang))
            with patch("aiqq.services.ai.chat_runtime.SEARCH_TIMEOUT_SECONDS", 0.05):
                response = await self.post(bridge, json={})
            self.assertEqual(response.status_code, 504)
            self.assertEqual(response.json()["error"]["code"], "timeout")
            self.assertTrue(cancelled.is_set())
            entered.clear()
            cancelled.clear()
            request_task = asyncio.create_task(self.post(bridge, json={}))
            await asyncio.wait_for(entered.wait(), 2)
        await asyncio.wait_for(cancelled.wait(), 2)
        with self.assertRaises(httpx.RemoteProtocolError):
            await request_task
        self.assertFalse(bridge._handlers)

    async def test_actual_cli_web_tool_receives_success_and_http_failure(self):
        if sys.platform != "linux":
            self.skipTest("Linux confinement")
        cli = find_codex_cli()
        if "codex-linux" not in cli:
            self.skipTest("packaged Codex CLI not installed")
        for status in (200, 403):
            with self.subTest(status=status):
                model_requests, search_requests = [], []
                marker = "SYNTHETIC_SEARCH_HIT_7429"
                # web.run sends the complete model conversation, not just the
                # small query. Large intact histories must remain searchable.
                model_input = "Find synthetic documentation.\n" + "完整历史消息\n" * 16000
                async def native_endpoint(request, body):
                    payload = json.loads(body)
                    self.assertEqual(request.headers["Authorization"], "Bearer provider-secret")
                    if request.path.endswith("/alpha/search"):
                        search_requests.append(payload)
                        return web.json_response({"output": marker, "results": [], "encrypted_output": ""}, status=status)
                    self.assertTrue(request.path.endswith("/responses"))
                    model_requests.append(payload)
                    number = len(model_requests)
                    if number == 1:
                        item = {"id": "call1", "type": "function_call", "call_id": "call1",
                            "name": "run", "namespace": "web", "arguments": json.dumps({
                                "search_query": [{"q": "synthetic documentation"}], "response_length": "short"}),
                            "status": "completed"}
                    else:
                        item = {"id": "message1", "type": "message", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": '{"ok":true}', "annotations": []}]}
                    response = {"id": f"response{number}", "object": "response", "model": payload["model"],
                        "status": "completed", "output": [item], "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
                    events = [{"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
                        {"type": "response.output_item.added", "output_index": 0, "item": item}]
                    if number > 1:
                        events.append({"type": "response.output_text.delta", "output_index": 0,
                            "content_index": 0, "delta": '{"ok":true}'})
                    events.extend([{"type": "response.output_item.done", "output_index": 0, "item": item},
                        {"type": "response.completed", "response": response}])
                    return web.Response(text="".join("data: " + json.dumps(event) + "\n\n" for event in events),
                        content_type="text/event-stream")
                self.custom_handler = native_endpoint
                root = Path(self.folder.name, f"native-{status}")
                backend = CodexSDKBackend(api_key="provider-secret", base_url=self.base_url,
                    model="gpt-6-astra", cli_path=cli, runtime_dir=root / "runtime", work_dir=root / "work")
                try:
                    async with asyncio.timeout(30):
                        result = await backend.run(instructions="Search and return JSON.", model_input=model_input,
                            output_schema=SCHEMA, enable_web_search=True, chat_read_session=ReadSession())
                finally:
                    await backend.close()
                self.assertEqual(result.text, '{"ok":true}')
                self.assertEqual(len(model_requests), 2)
                self.assertEqual(len(search_requests), 1)
                self.assertEqual(search_requests[0]["commands"]["search_query"], [{"q": "synthetic documentation"}])
                texts = [part.get("text", "") for item in search_requests[0]["input"]
                         for part in item.get("content", []) if isinstance(part, dict)]
                self.assertTrue(any(model_input in text for text in texts))
                outputs = [item["output"] for item in model_requests[1]["input"] if item.get("type") == "function_call_output"]
                if status == 200:
                    self.assertEqual(outputs, [[{"type": "input_text", "text": marker}]])
                else:
                    self.assertEqual(outputs, ["aborted"])
                self.assertFalse(list((root / "runtime").iterdir()))
                self.assertFalse(list((root / "work").iterdir()))
