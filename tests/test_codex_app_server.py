import asyncio
import base64
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

from codex_app_server import (
    CHAT_CONFIGURATION_FINGERPRINT_FILE,
    PROVIDER_ID,
    CodexAppServerClient,
    CodexAppServerError,
    _TurnState,
    extract_selected_search_image,
    find_codex_cli,
    format_model_input,
)
from codex_image import CodexGeneratedImage


PROJECT_CODEX = Path(__file__).resolve().parents[1] / "node_modules/.bin/codex"
PROJECT_NATIVE_CODEX = (
    Path(__file__).resolve().parents[1]
    / "node_modules/@openai/codex-linux-x64/vendor"
    / "x86_64-unknown-linux-musl/bin/codex"
)


class CodexAppServerTests(unittest.TestCase):
    def make_client(self, root: str) -> CodexAppServerClient:
        return CodexAppServerClient(
            api_key="secret-api-key",
            base_url="https://proxy.example/v1",
            model="gpt-test",
            cli_path=str(PROJECT_CODEX),
            runtime_dir=str(Path(root) / "runtime"),
            work_dir=str(Path(root) / "work"),
        )

    @staticmethod
    def mark_chat_configuration_current(
        client: CodexAppServerClient,
        *,
        instructions: str,
        enable_web_search: bool = False,
        enable_image_generation: bool = False,
    ) -> None:
        fingerprint = client._chat_configuration_fingerprint(
            instructions=instructions,
            enable_web_search=enable_web_search,
            enable_image_generation=enable_image_generation,
        )
        client.runtime_dir.mkdir(parents=True, exist_ok=True)
        (client.runtime_dir / CHAT_CONFIGURATION_FINGERPRINT_FILE).write_text(
            fingerprint + "\n",
            encoding="ascii",
        )

    def test_runtime_status_reports_process_and_turns(self):
        with tempfile.TemporaryDirectory() as root:
            client = self.make_client(root)
            self.assertFalse(client.is_running)
            self.assertEqual(client.active_turn_count, 0)

            client._process = SimpleNamespace(returncode=None)
            client._turns["thread-1"] = object()

            self.assertTrue(client.is_running)
            self.assertEqual(client.active_turn_count, 1)

    def test_conversation_roles_are_preserved_as_untrusted_json(self):
        value = format_model_input(
            [
                {"role": "user", "content": "前一个问题"},
                {"role": "assistant", "content": "前一个回答"},
                {"role": "user", "content": "继续说"},
            ]
        )

        self.assertIn('"role": "assistant"', value)
        self.assertIn('"content": "继续说"', value)
        self.assertIn("不可信的对话文本", value)

    def test_only_finally_selected_search_image_is_returned(self):
        text, selected = extract_selected_search_image(
            "这是正确画面。\n\n"
            "[[aiqq_image_url:https://images.example/correct.jpg]]"
        )

        self.assertEqual(text, "这是正确画面。")
        self.assertEqual(selected, ("https://images.example/correct.jpg",))

    def test_missing_or_unsafe_final_image_information_is_not_returned(self):
        plain_text, unselected = extract_selected_search_image("只返回文字。")
        cleaned_text, unsafe = extract_selected_search_image(
            "没有可靠图片。\n[[aiqq_image_url:http://127.0.0.1/x.jpg]]"
        )

        self.assertEqual(plain_text, "只返回文字。")
        self.assertEqual(unselected, ())
        self.assertEqual(cleaned_text, "没有可靠图片。")
        self.assertEqual(unsafe, ())

    def test_final_image_information_is_returned_without_current_search_event(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as root:
                client = self.make_client(root)
                state = _TurnState(
                    "thread-1",
                    asyncio.get_running_loop().create_future(),
                    None,
                )
                await client._complete_turn(
                    state,
                    {
                        "status": "completed",
                        "items": [
                            {
                                "type": "agentMessage",
                                "phase": "final_answer",
                                "text": (
                                    "这是富士山。\n"
                                    "[[aiqq_image_url:"
                                    "https://images.example/fuji.jpg]]"
                                ),
                            }
                        ],
                    },
                )
                return await state.future

        result = asyncio.run(exercise())

        self.assertEqual(result.text, "这是富士山。")
        self.assertEqual(
            result.image_urls,
            ("https://images.example/fuji.jpg",),
        )

    @unittest.skipUnless(PROJECT_NATIVE_CODEX.is_file(), "Linux Codex binary missing")
    def test_default_cli_uses_native_binary_without_node_runtime(self):
        cli = Path(find_codex_cli())
        completed = subprocess.run(
            [str(cli), "--version"],
            capture_output=True,
            check=False,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
            text=True,
        )

        self.assertEqual(cli, PROJECT_NATIVE_CODEX.resolve())
        self.assertNotEqual(cli, PROJECT_CODEX.resolve())
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("codex-cli", completed.stdout)

    def test_command_forces_https_provider_and_disables_host_tools(self):
        with tempfile.TemporaryDirectory() as root:
            command = self.make_client(root)._command()

        joined = " ".join(command)
        self.assertIn("supports_websockets=false", joined)
        self.assertIn("supports_standalone_web_search=true", joined)
        self.assertIn("standalone_web_search", command)
        self.assertIn("image_generation", command)
        self.assertIn("skip_host_skill_discovery", command)
        self.assertIn('env_key="CODEX_API_KEY"', joined)
        self.assertIn("--strict-config", command)
        for feature in (
            "shell_tool",
            "unified_exec",
            "plugins",
            "skill_search",
            "multi_agent",
        ):
            self.assertIn(feature, command)
        self.assertNotIn("secret-api-key", joined)

    def test_child_environment_does_not_inherit_bot_secrets(self):
        with tempfile.TemporaryDirectory() as root:
            client = self.make_client(root)
            home = Path(root) / "home"
            with patch.dict(
                os.environ,
                {
                    "QQ_BOT_SECRET": "qq-secret",
                    "NOVELAI_MCP_TOKEN": "novelai-secret",
                    "PATH": "/usr/bin:/bin",
                },
                clear=True,
            ):
                env = client._sanitized_env(home)

        self.assertEqual(env["CODEX_API_KEY"], "secret-api-key")
        self.assertNotIn("QQ_BOT_SECRET", env)
        self.assertNotIn("NOVELAI_MCP_TOKEN", env)

    def test_each_thread_explicitly_sets_its_web_search_mode(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as root:
                client = self.make_client(root)
                client._ensure_started = AsyncMock()
                client._interrupt = AsyncMock()
                self.mark_chat_configuration_current(
                    client,
                    instructions="chat",
                    enable_web_search=True,
                )
                captured = []

                async def rpc(method, params, *, timeout):
                    captured.append((method, params, timeout))
                    if method == "thread/start":
                        return {"thread": {"id": f"thread-{len(captured)}"}}
                    if method == "turn/start":
                        thread_id = params["threadId"]
                        state = client._turns[thread_id]
                        state.future.set_result(type("Result", (), {"text": "ok"})())
                        return {"turn": {"id": "turn-1"}}
                    raise AssertionError(method)

                client._rpc = rpc
                await client.run(
                    instructions="chat",
                    model_input="search",
                    enable_web_search=True,
                )
                await client.run(
                    instructions="moderate",
                    model_input="do not search",
                    enable_web_search=False,
                    utility=True,
                )
                await client.run(
                    instructions="create one safe image",
                    model_input="draw a cat",
                    enable_image_generation=True,
                )

            thread_starts = [
                params for method, params, _ in captured if method == "thread/start"
            ]
            self.assertEqual(thread_starts[0]["config"]["web_search"], "live")
            self.assertEqual(thread_starts[1]["config"]["web_search"], "disabled")
            self.assertTrue(thread_starts[0]["ephemeral"])
            self.assertTrue(thread_starts[1]["ephemeral"])
            self.assertTrue(thread_starts[2]["ephemeral"])
            self.assertFalse(
                thread_starts[0]["config"]["features"]["image_generation"]
            )
            self.assertFalse(
                thread_starts[1]["config"]["features"]["image_generation"]
            )
            self.assertTrue(
                thread_starts[2]["config"]["features"]["image_generation"]
            )

    def test_local_image_input_is_staged_for_turn_and_removed_afterward(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as root:
                client = self.make_client(root)
                client._ensure_started = AsyncMock()
                client._interrupt = AsyncMock()
                captured_path = None

                output = BytesIO()
                Image.new("RGB", (24, 16), "green").save(
                    output, format="JPEG"
                )
                image = CodexGeneratedImage(output.getvalue(), "image/jpeg")

                async def rpc(method, params, *, timeout):
                    nonlocal captured_path
                    if method == "thread/start":
                        return {"thread": {"id": "vision-thread"}}
                    if method == "turn/start":
                        self.assertEqual(params["input"][0]["type"], "text")
                        image_input = params["input"][1]
                        self.assertEqual(image_input["type"], "localImage")
                        self.assertEqual(image_input["detail"], "high")
                        captured_path = Path(image_input["path"])
                        self.assertTrue(captured_path.is_file())
                        self.assertTrue(
                            captured_path.is_relative_to(client.work_dir)
                        )
                        state = client._turns[params["threadId"]]
                        state.future.set_result(
                            type("Result", (), {"text": "看到了图片"})()
                        )
                        return {"turn": {"id": "turn-1"}}
                    raise AssertionError(method)

                client._rpc = rpc
                await client.run(
                    instructions="analyze image",
                    model_input="图片内容是什么",
                    input_images=(image,),
                )
                self.assertIsNotNone(captured_path)
                self.assertFalse(captured_path.exists())

        asyncio.run(exercise())

    def test_stale_staged_images_are_cleaned_without_touching_other_files(self):
        with tempfile.TemporaryDirectory() as root:
            client = self.make_client(root)
            input_directory = client.work_dir / ".aiqq-input-images"
            input_directory.mkdir(parents=True)
            stale = input_directory / ("a" * 32 + ".jpg")
            unrelated = input_directory / "keep.txt"
            stale.write_bytes(b"stale")
            unrelated.write_text("keep", encoding="ascii")

            client._cleanup_staged_input_images()

            self.assertFalse(stale.exists())
            self.assertTrue(unrelated.exists())

    def test_completed_image_item_is_returned_with_final_text(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as root:
                client = self.make_client(root)
                loop = asyncio.get_running_loop()
                state = _TurnState(
                    "thread-1",
                    loop.create_future(),
                    None,
                    allow_image_generation=True,
                )
                output = BytesIO()
                Image.new("RGB", (16, 16), "blue").save(output, format="PNG")
                encoded = base64.b64encode(output.getvalue()).decode("ascii")

                await client._handle_item(
                    state,
                    {
                        "type": "imageGeneration",
                        "status": "completed",
                        "result": encoded,
                        "failure": None,
                    },
                    completed=True,
                )
                await client._complete_turn(
                    state,
                    {
                        "status": "completed",
                        "items": [
                            {
                                "type": "agentMessage",
                                "phase": "final_answer",
                                "text": "图片已经生成。",
                            }
                        ],
                    },
                )
                result = await state.future

            self.assertEqual(result.text, "图片已经生成。")
            self.assertEqual(len(result.images), 1)
            self.assertEqual(result.images[0].mime_type, "image/png")

        asyncio.run(exercise())

    def test_image_item_is_rejected_when_turn_did_not_enable_it(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as root:
                client = self.make_client(root)
                client._interrupt = AsyncMock()
                state = _TurnState(
                    "thread-1", asyncio.get_running_loop().create_future(), None
                )

                await client._handle_item(
                    state,
                    {
                        "type": "imageGeneration",
                        "status": "completed",
                        "result": "ignored",
                    },
                    completed=True,
                )
                await asyncio.sleep(0)

            self.assertEqual(state.unexpected_tool, "imageGeneration")
            client._interrupt.assert_awaited_once_with(state)

        asyncio.run(exercise())

        asyncio.run(exercise())

    def test_persistent_chat_thread_is_created_once_then_resumed(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as root:
                client = self.make_client(root)
                client._ensure_started = AsyncMock()
                client._interrupt = AsyncMock()
                captured = []

                async def rpc(method, params, *, timeout):
                    captured.append((method, params, timeout))
                    if method == "thread/list":
                        return {"data": [], "nextCursor": None}
                    if method == "thread/start":
                        return {"thread": {"id": "shared-thread"}}
                    if method == "thread/resume":
                        return {"thread": {"id": params["threadId"]}}
                    if method == "turn/start":
                        state = client._turns[params["threadId"]]
                        state.future.set_result(type("Result", (), {"text": "ok"})())
                        return {"turn": {"id": "turn-1"}}
                    raise AssertionError(method)

                client._rpc = rpc
                await client.run(
                    instructions="chat",
                    model_input="first user",
                    enable_web_search=True,
                    persistent=True,
                )
                await client.run(
                    instructions="chat",
                    model_input="second user",
                    enable_web_search=True,
                    persistent=True,
                )

            starts = [params for method, params, _ in captured if method == "thread/start"]
            resumes = [params for method, params, _ in captured if method == "thread/resume"]
            turns = [params for method, params, _ in captured if method == "turn/start"]
            self.assertEqual(len(starts), 1)
            self.assertFalse(starts[0]["ephemeral"])
            self.assertNotIn("projectId", starts[0])
            self.assertEqual(resumes[0]["threadId"], "shared-thread")
            self.assertEqual(resumes[0]["developerInstructions"], "chat")
            self.assertEqual(
                [turn["threadId"] for turn in turns],
                ["shared-thread", "shared-thread"],
            )

        asyncio.run(exercise())

    def test_existing_persistent_chat_thread_is_discovered_and_resumed(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as root:
                client = self.make_client(root)
                client._ensure_started = AsyncMock()
                client._interrupt = AsyncMock()
                self.mark_chat_configuration_current(
                    client,
                    instructions="chat",
                )
                captured = []

                async def rpc(method, params, *, timeout):
                    captured.append((method, params, timeout))
                    if method == "thread/list":
                        return {
                            "data": [{"id": "stored-thread"}],
                            "nextCursor": None,
                        }
                    if method == "thread/resume":
                        return {"thread": {"id": params["threadId"]}}
                    if method == "turn/start":
                        state = client._turns[params["threadId"]]
                        state.future.set_result(type("Result", (), {"text": "ok"})())
                        return {"turn": {"id": "turn-1"}}
                    raise AssertionError(method)

                client._rpc = rpc
                await client.run(
                    instructions="chat",
                    model_input="continue",
                    persistent=True,
                )

            methods = [method for method, _, _ in captured]
            self.assertEqual(methods, ["thread/list", "thread/resume", "turn/start"])
            list_params = captured[0][1]
            self.assertEqual(list_params["cwd"], str(client.work_dir))
            self.assertEqual(list_params["modelProviders"], [PROVIDER_ID])
            self.assertEqual(list_params["sourceKinds"], ["appServer"])

        asyncio.run(exercise())

    def test_legacy_thread_without_configuration_fingerprint_is_replaced(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as root:
                client = self.make_client(root)
                client._ensure_started = AsyncMock()
                client._interrupt = AsyncMock()
                captured = []
                list_calls = 0

                async def rpc(method, params, *, timeout):
                    nonlocal list_calls
                    captured.append((method, params, timeout))
                    if method == "thread/list":
                        list_calls += 1
                        return {
                            "data": (
                                [{"id": "legacy-thread"}]
                                if list_calls == 1
                                else []
                            ),
                            "nextCursor": None,
                        }
                    if method == "thread/archive":
                        return {}
                    if method == "thread/start":
                        return {"thread": {"id": "new-thread"}}
                    if method == "turn/start":
                        state = client._turns[params["threadId"]]
                        state.future.set_result(type("Result", (), {"text": "ok"})())
                        return {"turn": {"id": "turn-1"}}
                    raise AssertionError(method)

                client._rpc = rpc
                await client.run(
                    instructions="new chat protocol",
                    model_input="hello",
                    persistent=True,
                )

                fingerprint_path = (
                    client.runtime_dir / CHAT_CONFIGURATION_FINGERPRINT_FILE
                )
                stored_fingerprint = fingerprint_path.read_text(
                    encoding="ascii"
                ).strip()

            methods = [method for method, _, _ in captured]
            self.assertEqual(
                methods,
                [
                    "thread/list",
                    "thread/archive",
                    "thread/list",
                    "thread/start",
                    "turn/start",
                ],
            )
            self.assertEqual(captured[1][1]["threadId"], "legacy-thread")
            self.assertEqual(client._chat_thread_id, "new-thread")
            self.assertEqual(len(stored_fingerprint), 64)

        asyncio.run(exercise())

    def test_clear_chat_session_archives_all_shared_threads(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as root:
                client = self.make_client(root)
                client._ensure_started = AsyncMock()
                client._chat_thread_id = "thread-2"
                captured = []

                async def rpc(method, params, *, timeout):
                    captured.append((method, params, timeout))
                    if method == "thread/list":
                        return {
                            "data": [{"id": "thread-2"}, {"id": "thread-1"}],
                            "nextCursor": None,
                        }
                    if method == "thread/archive":
                        return {}
                    raise AssertionError(method)

                client._rpc = rpc
                await client.clear_chat_session()

            archived = [
                params["threadId"]
                for method, params, _ in captured
                if method == "thread/archive"
            ]
            self.assertEqual(archived, ["thread-2", "thread-1"])
            self.assertIsNone(client._chat_thread_id)

        asyncio.run(exercise())

    def test_empty_unmaterialized_thread_is_replaced(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as root:
                client = self.make_client(root)
                client._ensure_started = AsyncMock()
                client._interrupt = AsyncMock()
                client._chat_thread_id = "empty-thread"
                self.mark_chat_configuration_current(
                    client,
                    instructions="chat",
                )
                captured = []

                async def rpc(method, params, *, timeout):
                    captured.append((method, params, timeout))
                    if method in {"thread/resume", "thread/archive"}:
                        raise CodexAppServerError(
                            f"{method} failed: no rollout found for thread id"
                        )
                    if method == "thread/start":
                        return {"thread": {"id": "new-thread"}}
                    if method == "turn/start":
                        state = client._turns[params["threadId"]]
                        state.future.set_result(type("Result", (), {"text": "ok"})())
                        return {"turn": {"id": "turn-1"}}
                    raise AssertionError(method)

                client._rpc = rpc
                await client.run(
                    instructions="chat",
                    model_input="hello",
                    persistent=True,
                )

            methods = [method for method, _, _ in captured]
            self.assertEqual(
                methods,
                ["thread/resume", "thread/archive", "thread/start", "turn/start"],
            )
            self.assertEqual(client._chat_thread_id, "new-thread")

        asyncio.run(exercise())

    def test_clear_ignores_unmaterialized_cached_thread(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as root:
                client = self.make_client(root)
                client._ensure_started = AsyncMock()
                client._chat_thread_id = "empty-thread"

                async def rpc(method, params, *, timeout):
                    if method == "thread/list":
                        return {"data": [], "nextCursor": None}
                    if method == "thread/archive":
                        raise CodexAppServerError(
                            "thread/archive failed: no rollout found for thread id"
                        )
                    raise AssertionError(method)

                client._rpc = rpc
                await client.clear_chat_session()

            self.assertIsNone(client._chat_thread_id)

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
