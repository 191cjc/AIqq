import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiqq.logic.ports import AgentTurnResult
from aiqq.services.ai.codex_sdk import (
    CodexBackendError,
    CodexSDKBackend,
    _codex_config,
)
from aiqq.services.agents.schemas import PROMPT_AUDIT_OUTPUT_SCHEMA


def _event(event_type, **values):
    return SimpleNamespace(type=event_type, **values)


def _item(item_type, **values):
    return SimpleNamespace(type=item_type, **values)


def _successful_events(text="{}"):
    return (
        _event("thread.started", thread_id="thread-new"),
        _event("turn.started"),
        _event(
            "item.completed",
            item=_item("agent_message", id="message-1", text=text),
        ),
        _event(
            "turn.completed",
            usage={"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 3},
        ),
    )


class _FakeSDK:
    def __init__(self, event_sets):
        self.event_sets = list(event_sets)
        self.codex_options = []
        self.calls = []
        self.resume_count = 0

    def factory(self, options):
        self.codex_options.append(options)
        return _FakeCodex(self, self.event_sets.pop(0))


class _FakeCodex:
    def __init__(self, sdk, events):
        self.sdk = sdk
        self.events = events

    def start_thread(self, options):
        call = {"thread_options": options}
        self.sdk.calls.append(call)
        return _FakeThread(call, self.events)

    def resume_thread(self, *_args, **_kwargs):
        self.sdk.resume_count += 1
        raise AssertionError("resume_thread must not be used")


class _FakeThread:
    def __init__(self, call, events):
        self.call = call
        self.events = events

    async def run_streamed(self, model_input, turn_options):
        work = Path(self.call["thread_options"]["working_directory"])
        self.call.update(
            {
                "model_input": model_input,
                "turn_options": turn_options,
                "agents": (work / "AGENTS.md").read_text(encoding="utf-8"),
                "skill": (
                    work
                    / ".agents"
                    / "skills"
                    / "aiqq-gpt-image"
                    / "SKILL.md"
                ).read_text(encoding="utf-8")
                if (work / ".agents" / "skills" / "aiqq-gpt-image" / "SKILL.md").is_file()
                else None,
            }
        )

        async def iterate():
            for event in self.events:
                yield event

        return SimpleNamespace(events=iterate())


class CodexSDKBackendTests(unittest.IsolatedAsyncioTestCase):
    def make_backend(self, root, sdk, **overrides):
        cli = Path(root) / "codex"
        cli.write_text("", encoding="ascii")
        skill = Path(root) / "SKILL.md"
        skill.write_text(
            "---\nname: aiqq-gpt-image\ndescription: test\n---\n",
            encoding="ascii",
        )
        values = {
            "api_key": "secret",
            "base_url": "https://api.example.com/v1",
            "model": "test-model",
            "cli_path": str(cli),
            "runtime_dir": Path(root) / "runtime",
            "work_dir": Path(root) / "work",
            "skill_path": skill,
            "codex_factory": sdk.factory,
            "process_environment": {
                "PATH": "/usr/bin",
                "LANG": "C.UTF-8",
                "QQ_BOT_SECRET": "must-not-leak",
                "NOVELAI_MCP_TOKEN": "must-not-leak",
            },
        }
        values.update(overrides)
        return CodexSDKBackend(**values)

    async def test_image_mode_has_isolated_instructions_and_no_chat_tools(self):
        sdk = _FakeSDK((_successful_events(),))
        with tempfile.TemporaryDirectory() as root:
            backend = self.make_backend(root, sdk, image_generation_mode=True)
            await backend.run(
                instructions="generate one image",
                model_input="a circle",
                output_schema=PROMPT_AUDIT_OUTPUT_SCHEMA,
            )
            for options in ({"enable_web_search": True}, {"enable_gpt_image_skill": True}):
                with self.assertRaises(ValueError):
                    await backend.run(
                        instructions="image", model_input="circle",
                        output_schema=PROMPT_AUDIT_OUTPUT_SCHEMA, **options,
                    )
        call = sdk.calls[0]
        self.assertIn("native image_generation tool", call["agents"])
        self.assertNotIn("Native web search is the only tool", call["agents"])
        self.assertIsNone(call["skill"])
        self.assertFalse(call["thread_options"]["web_search_enabled"])
        self.assertEqual(len(sdk.calls), 1)

    def test_image_mode_disables_all_sdk_retries_without_changing_chat(self):
        import tomllib

        normal = tomllib.loads(_codex_config("https://example.test/v1", True))
        image = tomllib.loads(_codex_config(
            "https://example.test/v1", True, image_generation_mode=True
        ))
        for name in ("request_max_retries", "stream_max_retries"):
            self.assertEqual(normal["model_providers"]["aiqq_proxy"][name], 2)
            self.assertEqual(image["model_providers"]["aiqq_proxy"][name], 0)
        self.assertEqual(normal["web_search"], "live")
        self.assertFalse(normal["features"]["image_generation"])
        self.assertEqual(image["web_search"], "disabled")
        self.assertTrue(image["features"].pop("image_generation"))
        self.assertTrue(all(value is False for value in image["features"].values()))

    async def test_image_mode_keeps_cli_tool_event_allowlist_closed(self):
        sdk = _FakeSDK(((_event("item.started", item=_item("command_execution")),),))
        with tempfile.TemporaryDirectory() as root:
            backend = self.make_backend(root, sdk, image_generation_mode=True)
            with self.assertRaises(CodexBackendError):
                await backend.run(
                    instructions="image", model_input="circle",
                    output_schema=PROMPT_AUDIT_OUTPUT_SCHEMA,
                )

    async def test_every_run_uses_a_new_isolated_thread_and_cleans_workspace(self):
        sdk = _FakeSDK((_successful_events("first"), _successful_events("second")))
        with tempfile.TemporaryDirectory() as root:
            backend = self.make_backend(root, sdk)
            first = await backend.run(
                instructions="trusted audit instructions",
                model_input="untrusted first input",
                output_schema=PROMPT_AUDIT_OUTPUT_SCHEMA,
            )
            second = await backend.run(
                instructions="trusted chat instructions",
                model_input="untrusted second input",
                output_schema=PROMPT_AUDIT_OUTPUT_SCHEMA,
                enable_gpt_image_skill=True,
            )

            self.assertEqual(first, AgentTurnResult("first", usage={
                "input_tokens": 10,
                "cached_input_tokens": 2,
                "output_tokens": 3,
            }))
            self.assertEqual(second.text, "second")
            self.assertEqual(sdk.resume_count, 0)
            self.assertEqual(len(sdk.codex_options), 2)
            homes = [Path(options["env"]["CODEX_HOME"]) for options in sdk.codex_options]
            works = [
                Path(call["thread_options"]["working_directory"])
                for call in sdk.calls
            ]
            self.assertEqual(len(set(homes)), 2)
            self.assertEqual(len(set(works)), 2)
            self.assertTrue(all(not path.exists() for path in homes + works))

        first_call, second_call = sdk.calls
        self.assertIn("trusted audit instructions", first_call["agents"])
        self.assertNotIn("untrusted first input", first_call["agents"])
        self.assertIsNone(first_call["skill"])
        self.assertTrue(second_call["model_input"].startswith("$aiqq-gpt-image\n"))
        self.assertIn("name: aiqq-gpt-image", second_call["skill"])
        self.assertEqual(
            second_call["turn_options"]["output_schema"],
            PROMPT_AUDIT_OUTPUT_SCHEMA,
        )
        self.assertEqual(
            second_call["thread_options"]["model_reasoning_effort"], "high"
        )

    async def test_sdk_process_environment_is_an_explicit_allowlist(self):
        sdk = _FakeSDK((_successful_events(),))
        with tempfile.TemporaryDirectory() as root:
            backend = self.make_backend(root, sdk)
            await backend.run(
                instructions="audit",
                model_input="{}",
                output_schema=PROMPT_AUDIT_OUTPUT_SCHEMA,
            )
        env = sdk.codex_options[0]["env"]
        self.assertEqual(set(env), {"CODEX_HOME", "HOME", "LANG", "PATH"})
        self.assertNotIn("QQ_BOT_SECRET", env)
        self.assertNotIn("NOVELAI_MCP_TOKEN", env)

    async def test_missing_or_non_strict_schema_is_rejected_before_sdk(self):
        sdk = _FakeSDK(())
        with tempfile.TemporaryDirectory() as root:
            backend = self.make_backend(root, sdk)
            with self.assertRaises(ValueError):
                await backend.run(
                    instructions="audit", model_input="{}", output_schema={}
                )
            with self.assertRaises(ValueError):
                await backend.run(
                    instructions="audit",
                    model_input="{}",
                    output_schema={"type": "object", "required": []},
                )
        self.assertEqual(sdk.codex_options, [])

    async def test_web_search_reports_progress_and_disabled_tools_fail_closed(self):
        search_events = (
            _event(
                "item.completed",
                item=_item("web_search", id="search-1", query="current facts"),
            ),
            *_successful_events("result"),
        )
        tool_events = (
            _event(
                "item.started",
                item=_item(
                    "command_execution",
                    id="command-1",
                    command="pwd",
                    aggregated_output="",
                    status="in_progress",
                ),
            ),
        )
        sdk = _FakeSDK((search_events, tool_events))
        progress = AsyncMock()
        with tempfile.TemporaryDirectory() as root:
            backend = self.make_backend(root, sdk)
            result = await backend.run(
                instructions="chat",
                model_input="{}",
                output_schema=PROMPT_AUDIT_OUTPUT_SCHEMA,
                enable_web_search=True,
                on_progress=progress,
            )
            self.assertEqual(result.text, "result")
            progress.assert_awaited_once_with("已完成检索：current facts。")
            with self.assertRaises(CodexBackendError):
                await backend.run(
                    instructions="chat",
                    model_input="{}",
                    output_schema=PROMPT_AUDIT_OUTPUT_SCHEMA,
                )

    async def test_slow_turn_continues_without_being_aborted(self):
        stream_started = asyncio.Event()
        release = asyncio.Event()

        class SlowThread(_FakeThread):
            async def run_streamed(self, model_input, turn_options):
                self.call["signal"] = turn_options["signal"]

                async def iterate():
                    stream_started.set()
                    await release.wait()
                    for event in _successful_events("late result"):
                        yield event

                return SimpleNamespace(events=iterate())

        class SlowCodex(_FakeCodex):
            def start_thread(self, options):
                call = {"thread_options": options}
                self.sdk.calls.append(call)
                return SlowThread(call, ())

        sdk = _FakeSDK(((),))

        def factory(options):
            sdk.codex_options.append(options)
            return SlowCodex(sdk, sdk.event_sets.pop(0))

        sdk.factory = factory
        with tempfile.TemporaryDirectory() as root:
            backend = self.make_backend(root, sdk, turn_timeout_seconds=0.01)
            run = asyncio.create_task(
                backend.run(
                    instructions="chat",
                    model_input="{}",
                    output_schema=PROMPT_AUDIT_OUTPUT_SCHEMA,
                )
            )
            await stream_started.wait()
            await asyncio.sleep(0.03)

            self.assertFalse(run.done())
            self.assertFalse(sdk.calls[0]["signal"].aborted)

            release.set()
            result = await asyncio.wait_for(run, 0.2)

        self.assertEqual(result.text, "late result")
        self.assertFalse(sdk.calls[0]["signal"].aborted)

    def test_partial_workspace_creation_is_cleaned(self):
        sdk = _FakeSDK(())
        with tempfile.TemporaryDirectory() as root:
            backend = self.make_backend(root, sdk)
            real_mkdtemp = tempfile.mkdtemp
            created_runtime = None

            def fail_work_directory(*args, **kwargs):
                nonlocal created_runtime
                if created_runtime is None:
                    created_runtime = Path(real_mkdtemp(*args, **kwargs))
                    return str(created_runtime)
                raise OSError("work directory unavailable")

            with patch(
                "aiqq.services.ai.codex_sdk.tempfile.mkdtemp",
                side_effect=fail_work_directory,
            ):
                with self.assertRaises(OSError):
                    backend._create_workspace(
                        instructions="chat",
                        enable_web_search=False,
                        enable_gpt_image_skill=False,
                    )

            self.assertIsNotNone(created_runtime)
            self.assertFalse(created_runtime.exists())

    async def test_cancelling_run_aborts_and_finishes_the_event_stream(self):
        stream_started = asyncio.Event()
        stream_finished = asyncio.Event()

        class WaitingThread(_FakeThread):
            async def run_streamed(self, model_input, turn_options):
                self.call["signal"] = turn_options["signal"]

                async def iterate():
                    stream_started.set()
                    try:
                        await turn_options["signal"].wait()
                    finally:
                        stream_finished.set()
                    if False:
                        yield None

                return SimpleNamespace(events=iterate())

        class WaitingCodex(_FakeCodex):
            def start_thread(self, options):
                call = {"thread_options": options}
                self.sdk.calls.append(call)
                return WaitingThread(call, ())

        sdk = _FakeSDK(((),))

        def factory(options):
            sdk.codex_options.append(options)
            return WaitingCodex(sdk, sdk.event_sets.pop(0))

        sdk.factory = factory
        with tempfile.TemporaryDirectory() as root:
            backend = self.make_backend(root, sdk)
            run = asyncio.create_task(
                backend.run(
                    instructions="chat",
                    model_input="{}",
                    output_schema=PROMPT_AUDIT_OUTPUT_SCHEMA,
                )
            )
            await stream_started.wait()
            run.cancel()
            try:
                with self.assertRaises(asyncio.CancelledError):
                    await run
                self.assertTrue(sdk.calls[0]["signal"].aborted)
                await asyncio.wait_for(stream_finished.wait(), 0.2)
            finally:
                sdk.calls[0]["signal"]._abort("test cleanup")

    async def test_close_waits_for_active_turn_cleanup(self):
        stream_started = asyncio.Event()

        class WaitingThread(_FakeThread):
            async def run_streamed(self, model_input, turn_options):
                self.call["signal"] = turn_options["signal"]

                async def iterate():
                    stream_started.set()
                    await turn_options["signal"].wait()
                    if False:
                        yield None

                return SimpleNamespace(events=iterate())

        class WaitingCodex(_FakeCodex):
            def start_thread(self, options):
                call = {"thread_options": options}
                self.sdk.calls.append(call)
                return WaitingThread(call, ())

        sdk = _FakeSDK(((),))

        def factory(options):
            sdk.codex_options.append(options)
            return WaitingCodex(sdk, sdk.event_sets.pop(0))

        sdk.factory = factory
        with tempfile.TemporaryDirectory() as root:
            backend = self.make_backend(root, sdk)
            run = asyncio.create_task(
                backend.run(
                    instructions="chat",
                    model_input="{}",
                    output_schema=PROMPT_AUDIT_OUTPUT_SCHEMA,
                )
            )
            await stream_started.wait()

            await backend.close()

            self.assertTrue(run.done())
            with self.assertRaises(CodexBackendError):
                await run
            homes = [Path(options["env"]["CODEX_HOME"]) for options in sdk.codex_options]
            works = [
                Path(call["thread_options"]["working_directory"])
                for call in sdk.calls
            ]
            self.assertTrue(all(not path.exists() for path in homes + works))

    async def test_official_sdk_launches_cli_and_parses_jsonl_events(self):
        with tempfile.TemporaryDirectory() as root:
            cli = Path(root) / "fake-codex"
            cli.write_text(
                """#!/usr/bin/python3
import json
import os
import sys

payload = json.dumps(
    {
        "argv": sys.argv[1:],
        "prompt": sys.stdin.read(),
        "env_keys": sorted(os.environ),
    },
    separators=(",", ":"),
)
events = [
    {"type": "thread.started", "thread_id": "thread-process-test"},
    {"type": "turn.started"},
    {
        "type": "item.completed",
        "item": {"id": "message-1", "type": "agent_message", "text": payload},
    },
    {
        "type": "turn.completed",
        "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1},
    },
]
for event in events:
    print(json.dumps(event), flush=True)
""",
                encoding="ascii",
            )
            os.chmod(cli, 0o700)
            backend = CodexSDKBackend(
                api_key="secret",
                base_url="https://api.example.com/v1",
                model="test-model",
                cli_path=str(cli),
                runtime_dir=Path(root) / "runtime",
                work_dir=Path(root) / "work",
                process_environment={"PATH": "/usr/bin", "QQ_BOT_SECRET": "hidden"},
            )

            result = await backend.run(
                instructions="audit",
                model_input="request payload",
                output_schema=PROMPT_AUDIT_OUTPUT_SCHEMA,
            )

        invocation = json.loads(result.text)
        self.assertEqual(invocation["argv"][:2], ["exec", "--experimental-json"])
        self.assertIn("--output-schema", invocation["argv"])
        self.assertIn("--skip-git-repo-check", invocation["argv"])
        self.assertNotIn("resume", invocation["argv"])
        self.assertEqual(invocation["prompt"], "request payload")
        self.assertIn("CODEX_API_KEY", invocation["env_keys"])
        self.assertNotIn("QQ_BOT_SECRET", invocation["env_keys"])


if __name__ == "__main__":
    unittest.main()
