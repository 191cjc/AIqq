import asyncio
import ctypes
import io
import json
import os
from pathlib import Path
import socket
import shlex
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

from aiohttp import web
import httpx
from PIL import Image

from aiqq.logic.models import ImageAsset
from aiqq.services.ai.chat_runtime import ChatRuntime, cleanup_orphan_turns, write_owner_marker, owner_marker
from aiqq.services.ai.codex_sdk import CodexSDKBackend, find_codex_cli
from aiqq.services.ai.responses import ResponsesBackend, _format_input, _read_tools

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}},
          "required": ["ok"], "additionalProperties": False}


def _image(color="red"):
    output = io.BytesIO()
    Image.new("RGB", (24, 16), color).save(output, format="PNG")
    return ImageAsset(data=output.getvalue(), mime_type="image/png", width=24, height=16)


class ReadSession:
    def __init__(self):
        self.history_calls = []
        self.image_calls = []

    async def read_history(self, **filters):
        self.history_calls.append(filters)
        return {"status": "ok", "messages": [{"record_id": 4, "unknown": {"false": False}}],
                "next_cursor": 4, "next_version_cursor": 8, "next_event_cursor": 12,
                "has_more": True, "has_more_versions": False, "has_more_events": True,
                "pages_read": 1, "pages_remaining": 2}

    async def read_image(self, record_id, attachment_index=0, version_id=None):
        self.image_calls.append((record_id, attachment_index, version_id))
        return {"status": "ok", "record_id": record_id}, (_image(), _image("blue"))


class ChatRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_bridge_auth_schema_image_staging_and_lifetime(self):
        session = ReadSession()
        with tempfile.TemporaryDirectory() as root:
            work, runtime = Path(root, "work"), Path(root, "runtime")
            work.mkdir(); runtime.mkdir()
            async with ChatRuntime(session=session, work=work, runtime=runtime,
                                   cli="/usr/bin/true", api_key="real-secret",
                                   base_url="https://example.invalid", model="model") as bridge:
                async with httpx.AsyncClient(trust_env=False) as client:
                    bad = await client.post(bridge.url + "/history", json={})
                    self.assertEqual(bad.status_code, 401)
                    async def oversized():
                        yield b'{"keyword":"'
                        yield b"x" * 17000
                        yield b'"}'
                    too_large = await client.post(bridge.url + "/history", content=oversized(),
                        headers={"Authorization": "Bearer " + bridge.read_token, "Content-Type": "application/json"})
                    self.assertEqual(too_large.status_code, 413)
                    headers = {"Authorization": "Bearer " + bridge.read_token}
                    history = await client.post(bridge.url + "/history", json={"limit": 1}, headers=headers)
                    history.raise_for_status()
                    self.assertEqual(history.json(), {
                        "status": "ok", "full_records_delivery": "model_context", "page_number": 1,
                        "message_count": 1, "next_cursor": 4, "next_version_cursor": 8,
                        "next_event_cursor": 12, "has_more": True, "has_more_versions": False,
                        "has_more_events": True, "pages_read": 1, "pages_remaining": 2,
                    })
                    result = await client.post(bridge.url + "/image", json={"record_id": 4}, headers=headers)
                    result.raise_for_status()
                    images = result.json()["images"]
                    self.assertEqual(len(images), 2)
                    self.assertTrue(all(Path(item["path"]).is_file() for item in images))
                    refused = await client.post(bridge.url + "/image", json={"record_id": 4, "group_id": "other"}, headers=headers)
                    self.assertEqual(refused.status_code, 400)
                    denied = await client.post(bridge.url + "/v1/responses", json={"model": "model"}, headers=headers)
                    self.assertEqual(denied.status_code, 401)
                    self.assertNotIn("real-secret", (runtime / "isolation.json").read_text())
                    url = bridge.url
            async with httpx.AsyncClient(trust_env=False) as client:
                with self.assertRaises(httpx.ConnectError):
                    await client.post(url + "/history", json={})

    async def test_image_staging_cannot_follow_child_symlink_outside_work(self):
        with tempfile.TemporaryDirectory() as root:
            work, runtime, outside = [Path(root, name) for name in ("work", "runtime", "outside")]
            for directory in (work, runtime, outside): directory.mkdir()
            (work / "read-images").symlink_to(outside, target_is_directory=True)
            async with ChatRuntime(session=ReadSession(), work=work, runtime=runtime,
                                   cli="/usr/bin/true", api_key="secret",
                                   base_url="https://unused.invalid", model="model") as bridge:
                async with httpx.AsyncClient(trust_env=False) as client:
                    result = await client.post(bridge.url + "/image", json={"record_id": 4},
                        headers={"Authorization": "Bearer " + bridge.read_token})
                    self.assertEqual(result.status_code, 400)
                self.assertFalse(list(outside.iterdir()))

    async def test_cancel_closes_inflight_read_and_removes_temporary_images(self):
        entered, finished = asyncio.Event(), asyncio.Event()
        class WaitingSession(ReadSession):
            async def read_image(self, **kwargs):
                entered.set()
                try:
                    await asyncio.Future()
                finally:
                    finished.set()
        class FakeCodex:
            def __init__(self, options): self.options = options
            def start_thread(self, options): return self
            async def run_streamed(self, *_args):
                async def events():
                    async with httpx.AsyncClient(trust_env=False) as client:
                        await client.post(self.options["env"]["AIQQ_CHAT_READ_URL"] + "/image",
                            json={"record_id": 4}, headers={"Authorization": "Bearer " + self.options["env"]["AIQQ_CHAT_READ_TOKEN"]})
                    if False: yield None
                return SimpleNamespace(events=events())
        with tempfile.TemporaryDirectory() as root:
            backend = CodexSDKBackend(api_key="secret", base_url="https://unused.invalid", model="model",
                cli_path="/usr/bin/true", runtime_dir=Path(root, "runtime"), work_dir=Path(root, "work"),
                codex_factory=FakeCodex)
            run = asyncio.create_task(backend.run(instructions="chat", model_input="read", output_schema=SCHEMA,
                input_images=(_image(),), chat_read_session=WaitingSession()))
            await asyncio.wait_for(entered.wait(), 3)
            run.cancel()
            with self.assertRaises(asyncio.CancelledError): await run
            await asyncio.wait_for(finished.wait(), 3)
            self.assertFalse(list(Path(root, "runtime").iterdir()))
            self.assertFalse(list(Path(root, "work").iterdir()))

    async def test_actual_cli_executes_script_then_sends_viewed_pixels(self):
        """Real CLI/tools under service umask; synthetic SSE, no provider call."""
        if sys.platform != "linux":
            self.skipTest("Linux confinement")
        cli = find_codex_cli()
        if "codex-linux" not in cli:
            self.skipTest("packaged Codex CLI not installed")
        page = {"status": "ok", "messages": [{"record_id": 4, "payload": {
            "content": "完整正文\n" * 6000, "unknown_tail": {"null": None, "false": False, "zero": 0}}}],
            "has_more": False, "records_complete": True}
        class NativeSession(ReadSession):
            async def read_history(self, **filters):
                self.history_calls.append(filters)
                return page
        session = NativeSession()
        requests = []
        background_pids = []
        permission_reports = []
        with tempfile.TemporaryDirectory() as root:
            other_turn = Path(root, "other-turn")
            other_turn.mkdir(mode=0o700)
            secret = other_turn / "private-record"
            secret.write_text("unreachable")
            async def endpoint(request):
                payload = await request.json()
                requests.append(payload)
                number = len(requests)
                self.assertEqual(request.headers["Authorization"], "Bearer real-secret")
                if number == 1:
                    permission_code = f'''
import json,os
from pathlib import Path
result={{"umask":os.umask(0o022),"work_mode":Path.cwd().stat().st_mode & 0o777}}
Path("tool-created").write_text("temporary")
result["file_mode"]=Path("tool-created").stat().st_mode & 0o777
Path("outside-link").symlink_to({str(secret)!r})
for name,path in {{"other_turn":{str(secret)!r},"symlink":"outside-link",
                  "database":"/var/lib/aiqq/group_messages.db",
                  "config":"/home/ubuntu/AiQQ/.env","proc":"/proc/self/environ"}}.items():
 try:
  with open(path,"rb"):pass
  result[name]="allowed"
 except OSError:result[name]="denied"
try:os.chmod("tool-created",0o777);result["chmod"]="allowed"
except OSError:result["chmod"]="denied"
with open("tool-created","rb") as handle:
 try:os.fchmod(handle.fileno(),0o777);result["fchmod"]="allowed"
 except OSError:result["fchmod"]="denied"
Path("permissions.json").write_text(json.dumps(result))
'''
                    background_code = (
                        "import subprocess;from pathlib import Path;"
                        "p=subprocess.Popen(['/usr/bin/python3','-c','import time;time.sleep(60)'],"
                        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,"
                        "start_new_session=True,env={});Path('background.pid').write_text(str(p.pid))"
                    )
                    command = "python3 -c " + shlex.quote(permission_code) + "\npython3 -c " + shlex.quote(background_code) + "\npython3 .agents/skills/aiqq-chat-read/scripts/read_history.py --limit 50\npython3 .agents/skills/aiqq-chat-read/scripts/read_image.py --record-id 4"
                    item = {"id": "call1", "type": "custom_tool_call", "call_id": "call1",
                            "name": "exec", "status": "completed",
                            "input": "text(await tools.exec_command(" + json.dumps({"cmd": command, "login": False}) + "));"}
                elif number in (2, 3):
                    if number == 2:
                        pid_path = next(Path(root, "work").glob("turn-*/background.pid"))
                        background_pids.append(int(pid_path.read_text()))
                        report_path = pid_path.with_name("permissions.json")
                        permission_reports.append(json.loads(report_path.read_text()))
                        for parent in ("work", "runtime"):
                            turn = next(Path(root, parent).glob("turn-*"))
                            self.assertEqual(turn.stat().st_mode & 0o777, 0o700)
                        parent_file = Path(root, "parent-created")
                        parent_file.write_text("still private")
                        self.assertEqual(parent_file.stat().st_mode & 0o777, 0o600)
                    image_paths = sorted(Path(root, "work").glob("turn-*/read-images/*.png"))
                    self.assertEqual(len(image_paths), 2)
                    image_path = image_paths[number - 2]
                    item = {"id": f"call{number}", "type": "function_call", "call_id": f"call{number}",
                            "name": "view_image", "arguments": json.dumps({"path": str(image_path)}), "status": "completed"}
                else:
                    item = {"id": "msg1", "type": "message", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": '{"ok":true}', "annotations": []}]}
                response = {"id": f"resp{number}", "object": "response", "model": payload["model"],
                            "status": "completed", "output": [item],
                            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
                events = [{"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
                          {"type": "response.output_item.added", "output_index": 0, "item": item}]
                if number > 3:
                    events.append({"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "delta": '{"ok":true}'})
                events.extend([{"type": "response.output_item.done", "output_index": 0, "item": item},
                               {"type": "response.completed", "response": response}])
                return web.Response(text="".join("data: " + json.dumps(event) + "\n\n" for event in events), content_type="text/event-stream")

            app = web.Application(); app.router.add_post("/responses", endpoint)
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0); await site.start()
            port = site._server.sockets[0].getsockname()[1]
            backend = CodexSDKBackend(api_key="real-secret", base_url=f"http://127.0.0.1:{port}",
                model="gpt-5.6-sol", cli_path=cli, runtime_dir=Path(root, "runtime"), work_dir=Path(root, "work"))
            original_umask = os.umask(0o077)
            try:
                async with asyncio.timeout(30):
                    result = await backend.run(instructions="Read requested images and return JSON.", model_input="Read record 4",
                        output_schema=SCHEMA, chat_read_session=session)
            finally:
                observed_parent_umask = os.umask(original_umask)
                await backend.close(); await runner.cleanup()
            self.assertEqual(observed_parent_umask, 0o077)
            self.assertEqual(permission_reports, [{
                "umask": 0o022, "work_mode": 0o700, "file_mode": 0o644,
                "other_turn": "denied", "symlink": "denied", "database": "denied",
                "config": "denied", "proc": "denied", "chmod": "denied", "fchmod": "denied",
            }])
            self.assertEqual(result.text, '{"ok":true}')
            self.assertEqual(session.image_calls, [(4, 0, None)])
            self.assertEqual(session.history_calls, [{"limit": 50}])
            for request in requests[1:]:
                full_context = json.loads(request["input"][-1]["content"][0]["text"])
                self.assertEqual(full_context["untrusted_chat_read_results"], [page])
            # The large original page is injected intact outside shell stdout.
            self.assertGreater(len(page["messages"][0]["payload"]["content"]), 10000)
            self.assertEqual(len(requests), 4)
            self.assertTrue(background_pids)
            self.assertTrue(all(not Path(f"/proc/{pid}").exists() for pid in background_pids))
            outputs = [item for item in requests[-1]["input"] if item.get("type") == "function_call_output"]
            pixels = [part["image_url"] for item in outputs for part in item.get("output", [])
                      if isinstance(part, dict) and part.get("type") == "input_image"]
            self.assertEqual(len(pixels), 2)
            self.assertEqual(len(set(pixels)), 2)
            self.assertTrue(all(value.startswith("data:image/") for value in pixels))
            self.assertFalse(list(Path(root, "runtime").iterdir()))
            self.assertFalse(list(Path(root, "work").iterdir()))

    async def test_linux_enforces_file_and_network_restrictions(self):
        if sys.platform != "linux":
            self.skipTest("Linux confinement")
        with tempfile.TemporaryDirectory() as root:
            work, runtime = Path(root, "work"), Path(root, "runtime")
            work.mkdir(); runtime.mkdir()
            secret = Path(root, "other-group-secret"); secret.write_text("unreachable")
            (work / "escape").symlink_to(secret)
            (work / "allowed").write_text("allowed")
            allowed_server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
            denied_server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
            allowed_port = allowed_server.sockets[0].getsockname()[1]
            denied_port = denied_server.sockets[0].getsockname()[1]
            from aiqq.services.ai import chat_isolation
            script = f'''
import importlib.util,json,os,socket
from pathlib import Path
spec=importlib.util.spec_from_file_location("isolation",{chat_isolation.__file__!r})
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
protected_fd=os.open({str(secret)!r},os.O_RDONLY)
module.confine(read_paths=["/usr/bin","/usr/lib","/lib","/lib64"],write_paths=[{str(work)!r}],ports=[{allowed_port}])
results={{}}
try:os.chmod({str(secret)!r},0o777);results["host_chmod"]="allowed"
except OSError:results["host_chmod"]="denied"
try:os.fchmod(protected_fd,0o777);results["host_fchmod"]="allowed"
except OSError:results["host_fchmod"]="denied"
os.close(protected_fd)
for name,path in {{"allowed":{str(work / 'allowed')!r},"other_group":{str(secret)!r},"symlink":{str(work / 'escape')!r},"proc":"/proc/self/environ","database":"/var/lib/aiqq/group_messages.db"}}.items():
 try:results[name]=Path(path).read_text()
 except OSError:results[name]="denied"
for name,port in {{"allowed_tcp":{allowed_port},"other_tcp":{denied_port}}}.items():
 try:
  s=socket.socket();s.settimeout(2);s.connect(("127.0.0.1",port));s.close();results[name]="connected"
 except OSError:results[name]="denied"
for name,args in {{"unix":(socket.AF_UNIX,socket.SOCK_STREAM),"udp":(socket.AF_INET,socket.SOCK_DGRAM)}}.items():
 try:s=socket.socket(*args);s.close();results[name]="allowed"
 except OSError:results[name]="denied"
print(json.dumps(results))
'''
            try:
                process = await asyncio.create_subprocess_exec("/usr/bin/python3", "-c", script,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, stderr = await process.communicate()
                self.assertEqual(process.returncode, 0, stderr.decode())
            finally:
                allowed_server.close(); denied_server.close()
                await allowed_server.wait_closed(); await denied_server.wait_closed()
            result = json.loads(stdout)
            self.assertEqual(result.pop("allowed"), "allowed")
            self.assertEqual(result.pop("allowed_tcp"), "connected")
            self.assertTrue(all(value == "denied" for value in result.values()), result)

    def test_orphan_cleanup_preserves_active_turns_and_unowned_paths(self):
        with tempfile.TemporaryDirectory() as root:
            active, dead, unknown = [Path(root, name) for name in ("turn-active", "turn-dead", "turn-unknown")]
            for directory in (active, dead, unknown): directory.mkdir()
            write_owner_marker(active)
            owner_marker(dead).write_text(json.dumps({"pid": os.getpid(), "start": "stale-process-start"}))
            (dead / "pixels.png").write_bytes(b"old temporary data")
            cleanup_orphan_turns(Path(root))
            self.assertTrue(active.exists()); self.assertTrue(unknown.exists()); self.assertFalse(dead.exists())

    def test_multiple_input_images_are_kept_or_rejected_explicitly(self):
        images = (_image(), _image("blue"))
        self.assertEqual(len(_format_input("describe", images)[0]["content"]), 3)
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(len(CodexSDKBackend._stage_input_images(Path(root), images)), 2)
        with self.assertRaises(ValueError): _format_input("describe", images * 5)

    def test_responses_read_tool_schema_has_complete_paging_and_no_group_override(self):
        tools = {tool["name"]: tool for tool in _read_tools()}
        self.assertEqual(set(tools), {"read_history", "read_image"})
        history_fields = {"record_id", "before_record_id", "message_id", "limit", "sender",
                          "keyword", "sent_after", "sent_before", "versions", "before_version_id",
                          "events", "before_event_record_id"}
        self.assertEqual(set(tools["read_history"]["parameters"]["properties"]), history_fields)
        for tool in tools.values():
            self.assertTrue(tool["strict"])
            parameters = tool["parameters"]
            self.assertFalse(parameters["additionalProperties"])
            self.assertEqual(set(parameters["required"]), set(parameters["properties"]))
            self.assertNotIn("group_id", parameters["properties"])

    async def test_responses_read_loop_preserves_records_and_adds_all_pixels(self):
        requests = []
        class Responses:
            async def create(self, **kwargs):
                requests.append(kwargs)
                if len(requests) == 1:
                    return SimpleNamespace(output_text="", output=[{"type": "function_call", "name": "read_image",
                        "call_id": "call1", "arguments": '{"record_id":4,"attachment_index":0,"version_id":null}'}])
                return SimpleNamespace(output_text='{"ok":true}', output=[], usage=None)
        backend = ResponsesBackend(api_key="", base_url="https://unused.invalid", model="model",
                                   client=SimpleNamespace(responses=Responses()))
        result = await backend.run(instructions="chat", model_input="complete records", output_schema=SCHEMA, chat_read_session=ReadSession())
        self.assertEqual(result.text, '{"ok":true}')
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[1]["input"][0]["content"], "complete records")
        pixels = [part for item in requests[1]["input"] for part in item.get("content", []) if isinstance(part, dict) and part.get("type") == "input_image"]
        self.assertEqual(len(pixels), 2)
        self.assertNotIn("previous_response_id", requests[1])


class BackendFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_responses_read_loop_stops_after_bounded_rounds(self):
        requests = []
        class Responses:
            async def create(self, **kwargs):
                requests.append(kwargs)
                return SimpleNamespace(output_text="", output=[{"type": "function_call", "name": "read_history",
                    "call_id": str(len(requests)), "arguments": '{"limit":1}'}])
        from aiqq.exceptions import AgentUnavailable
        from aiqq.services.ai.chat_runtime import MAX_TOOL_ROUNDS
        backend = ResponsesBackend(api_key="", base_url="https://unused.invalid", model="model",
            client=SimpleNamespace(responses=Responses()))
        with self.assertRaises(AgentUnavailable):
            await backend.run(instructions="chat", model_input="history", output_schema=SCHEMA, chat_read_session=ReadSession())
        self.assertEqual(len(requests), MAX_TOOL_ROUNDS + 1)

    async def test_responses_context_error_is_preserved(self):
        from aiqq.exceptions import AgentContextTooLarge
        from openai import BadRequestError
        class Responses:
            async def create(self, **kwargs):
                raise BadRequestError("context_length_exceeded", response=httpx.Response(400,
                    request=httpx.Request("POST", "https://unused.invalid")), body={"code": "context_length_exceeded"})
        backend = ResponsesBackend(api_key="", base_url="https://unused.invalid", model="model",
            client=SimpleNamespace(responses=Responses()))
        with self.assertRaises(AgentContextTooLarge):
            await backend.run(instructions="chat", model_input="history", output_schema=SCHEMA)

    async def test_codex_context_error_is_preserved(self):
        from aiqq.exceptions import AgentContextTooLarge
        from openai_codex_sdk import AbortController
        async def events():
            yield SimpleNamespace(type="turn.failed", error=SimpleNamespace(message="context_length_exceeded"))
        with tempfile.TemporaryDirectory() as root:
            backend = CodexSDKBackend(api_key="secret", base_url="https://unused.invalid", model="model",
                cli_path="/usr/bin/true", runtime_dir=Path(root, "runtime"), work_dir=Path(root, "work"))
            with self.assertRaises(AgentContextTooLarge):
                await backend._consume_events(events(), controller=AbortController(), enable_web_search=False, on_progress=None)
