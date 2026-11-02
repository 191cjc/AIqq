import base64
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

from aiqq.config import AppConfig
from aiqq.bootstrap import build_application
from aiqq.exceptions import ConfigurationError, NovelAITagSearchUnavailable
from aiqq.interfaces.qq.command_router import ExplicitCommandRouter
from aiqq.interfaces.qq.commands import parse_command
from aiqq.interfaces.qq.handlers import IncomingGroupMessage
from aiqq.interfaces.qq.ui import feature_menu_keyboard
from aiqq.logic.models import (
    ConversationResult, DanbooruTag, ImageAsset, ImagePromptAuditResult, ImageUsageDecision,
)
from aiqq.logic.novelai import NovelAIImageWorkflow
from aiqq.services.images.novelai import (
    NAI_V5_MODEL,
    NOVELAI_IMAGE_SIZES,
    NaiV5Service,
    NovelAIError,
)
from aiqq.services.novelai_tags import NaiV5TagSearchService
from aiqq.services.qq.panel import command_items


def image_result():
    output = BytesIO()
    Image.new("RGB", (12, 8), "white").save(output, format="PNG")
    return {
        "content": [{
            "type": "image",
            "mimeType": "image/png",
            "data": base64.b64encode(output.getvalue()).decode("ascii"),
        }]
    }


class FakeV5Client:
    def __init__(self, result=None, *, tools=None):
        self.result = image_result() if result is None else result
        self.tools = tools if tools is not None else [{
            "name": "generate_image",
            "inputSchema": {"properties": {
                "prompt": {}, "model": {}, "quality": {}, "n_samples": {},
            }},
        }]
        self.calls = []
        self.closed = False

    async def list_tools(self):
        return {"tools": self.tools}

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result

    async def close(self):
        self.closed = True


class NaiV5ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_style_read_failure_names_v5_and_stops_before_audit_and_generation(self):
        auditor = SimpleNamespace(audit=AsyncMock())
        generator = SimpleNamespace(generate=AsyncMock())
        usage = SimpleNamespace(reserve_attempt=AsyncMock())
        workflow = NovelAIImageWorkflow(
            prompt_auditor=auditor, image_service=generator, image_usage=usage,
            styles=SimpleNamespace(load=AsyncMock(
                side_effect=RuntimeError("private-member secret-style")
            )),
            provider_name="Nai v5", command_name="Nai v5 作图", provider_id="nai_v5",
        )
        with self.assertLogs("aiqq.logic.novelai") as logs:
            result = await workflow.run(
                "一只白猫，窗边。", conversation_key="key", member_openid="private-member"
            )
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.error_code, "nai_v5_style_unavailable")
        self.assertEqual(result.summary, result.full_text)
        self.assertIn("Nai v5 画风读取失败", result.full_text)
        auditor.audit.assert_not_awaited()
        usage.reserve_attempt.assert_not_awaited()
        generator.generate.assert_not_awaited()
        self.assertNotIn("private-member", "\n".join(logs.output))
        self.assertNotIn("secret-style", "\n".join(logs.output))

    async def test_discovery_requires_new_generation_schema(self):
        old_client = FakeV5Client(tools=[{
            "name": "generate_image",
            "inputSchema": {"properties": {"prompt": {}, "model": {}}},
        }])
        service = NaiV5Service(old_client)
        with self.assertLogs("aiqq.services.images.novelai"):
            self.assertFalse(await service.check_connection())
        with self.assertRaises(NovelAIError) as error:
            await service.generate("white cat")
        self.assertEqual(error.exception.kind, "not_ready")
        self.assertEqual(old_client.calls, [])

    async def test_generation_uses_v5_contract_and_validates_inline_image(self):
        client = FakeV5Client()
        service = NaiV5Service(client)
        self.assertTrue(await service.check_connection())

        image = await service.generate("white cat", orientation="landscape")

        self.assertEqual((image.width, image.height), (12, 8))
        self.assertEqual(len(client.calls), 1)
        name, arguments = client.calls[0]
        self.assertEqual(name, "generate_image")
        self.assertEqual(arguments["model"], NAI_V5_MODEL)
        self.assertEqual(
            (arguments["width"], arguments["height"]),
            NOVELAI_IMAGE_SIZES["landscape"],
        )
        self.assertEqual(arguments["steps"], 28)
        self.assertIsInstance(arguments["seed"], int)
        self.assertGreaterEqual(arguments["seed"], 1)
        self.assertLessEqual(arguments["seed"], 2**32 - 1)
        self.assertEqual(arguments["n_samples"], 1)
        self.assertIs(arguments["quality"], False)
        self.assertNotIn("quality_toggle", arguments)
        self.assertNotIn("variety_boost", arguments)
        await service.close()
        self.assertTrue(client.closed)
        self.assertFalse(service.is_ready)

    async def test_repeated_prompt_gets_a_fresh_explicit_seed_for_each_request(self):
        client = FakeV5Client()
        service = NaiV5Service(client)
        await service.initialize()

        with patch(
            "aiqq.services.images.novelai.secrets.randbelow",
            side_effect=(0, 2**32 - 2, 41),
        ) as random_seed:
            for _ in range(3):
                await service.generate("white cat")

        self.assertEqual(
            [arguments["seed"] for _, arguments in client.calls],
            [1, 2**32 - 1, 42],
        )
        self.assertEqual(random_seed.call_count, 3)
        self.assertEqual(
            [arguments["prompt"] for _, arguments in client.calls],
            ["rating:general, safe, sfw, white cat"] * 3,
        )
        await service.close()

    async def test_generation_rejects_tool_errors_and_missing_image(self):
        for result in ({"isError": True}, {"content": [{"type": "text", "text": "done"}]}):
            client = FakeV5Client(result)
            service = NaiV5Service(client)
            await service.initialize()
            with self.assertRaises(NovelAIError):
                await service.generate("white cat")

    async def test_shared_workflow_charges_once_and_reports_v5_provider(self):
        client = FakeV5Client()
        service = NaiV5Service(client)
        await service.initialize()
        auditor = SimpleNamespace(audit=AsyncMock(return_value=ImagePromptAuditResult(
            True, True, "safe", "safe scene", "", "square",
        )))
        usage = SimpleNamespace(
            reserve_attempt=AsyncMock(return_value=ImageUsageDecision(True)),
            record_success=AsyncMock(),
            finish_attempt=AsyncMock(),
        )
        workflow = NovelAIImageWorkflow(
            prompt_auditor=auditor,
            image_service=service,
            image_usage=usage,
            styles=SimpleNamespace(load=AsyncMock(return_value="watercolor")),
            provider_name="Nai v5",
            command_name="Nai v5 作图",
            provider_id="nai_v5",
        )

        result = await workflow.run(
            "white cat", conversation_key="group:g:member:m", member_openid="m"
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.image_provenance["provider"], "nai_v5")
        self.assertEqual(result.generation_prompt, "white cat")
        self.assertEqual(client.calls[0][1]["prompt"],
                         "rating:general, safe, sfw, watercolor, white cat")
        usage.record_success.assert_awaited_once_with("group:g:member:m")
        usage.finish_attempt.assert_awaited_once_with("group:g:member:m")

    async def test_chinese_prompt_is_audited_without_english_requirement_and_preserved(self):
        client = FakeV5Client()
        service = NaiV5Service(client)
        await service.initialize()
        auditor = SimpleNamespace(audit=AsyncMock(return_value=ImagePromptAuditResult(
            True, True, "safe", "安全的画面描述", "", "square",
        )))
        usage = SimpleNamespace(
            reserve_attempt=AsyncMock(return_value=ImageUsageDecision(True)),
            record_success=AsyncMock(),
            finish_attempt=AsyncMock(),
        )
        workflow = NovelAIImageWorkflow(
            prompt_auditor=auditor,
            image_service=service,
            image_usage=usage,
            styles=SimpleNamespace(load=AsyncMock(return_value="watercolor")),
            provider_name="Nai v5",
            command_name="Nai v5 作图",
            provider_id="nai_v5",
            quota_provider="nai_v5",
        )

        result = await workflow.run(
            " 一只白猫，坐在窗边。 ",
            conversation_key="group:g:member:m",
            member_openid="m",
        )

        prompt = "watercolor, 一只白猫，坐在窗边。"
        self.assertEqual(result.status, "ok")
        auditor.audit.assert_awaited_once_with(prompt, require_english=False)
        self.assertEqual(result.generation_prompt, "一只白猫，坐在窗边。")
        self.assertEqual(
            client.calls[0][1]["prompt"],
            f"rating:general, safe, sfw, {prompt}",
        )


class NaiV5TagTests(unittest.IsolatedAsyncioTestCase):
    async def test_tag_schema_is_converted_and_bounded(self):
        client = FakeV5Client({"structuredContent": {"result": [
            {"text": "white_cat", "count": 120, "description": "cat"},
            {"text": "window", "count": 80},
        ]}})

        tags = await NaiV5TagSearchService(client).search("white cat", limit=2)

        self.assertEqual(tags, (
            DanbooruTag("white_cat", "general", 120),
            DanbooruTag("window", "general", 80),
        ))
        self.assertEqual(client.calls, [(
            "suggest_tags",
            {"prompt": "white_cat", "model": NAI_V5_MODEL, "language": "en"},
        )])

    async def test_text_records_and_malformed_records(self):
        client = FakeV5Client({"content": [
            {"type": "text", "text": json.dumps({"text": "white_cat", "count": 10})},
            {"type": "text", "text": json.dumps({"text": "window", "count": 5})},
        ]})
        self.assertEqual(len(await NaiV5TagSearchService(client).search("cat")), 2)
        client.result = {"structuredContent": {"result": [{"text": "非英文", "count": 1}]}}
        with self.assertLogs("aiqq.services.novelai_tags"):
            with self.assertRaises(NovelAITagSearchUnavailable):
                await NaiV5TagSearchService(client).search("cat")

    async def test_deployed_tagger_uses_tag_instead_of_documented_text(self):
        client = FakeV5Client({"structuredContent": {"result": [
            {"tag": "white cat", "count": 10000, "confidence": 0.88},
        ]}})

        self.assertEqual(
            await NaiV5TagSearchService(client).search("white cat"),
            (DanbooruTag("white cat", "general", 10000),),
        )


class NaiV5RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_panel_menu_and_command_route_to_v5_only(self):
        self.assertIn("Nai v5 作图", [item["name"] for item in command_items()])
        self.assertIn("Nai v5 作图", str(feature_menu_keyboard()))
        self.assertIsNone(parse_command("Nai v5 作图测试"))

        sender = SimpleNamespace(
            acknowledge_image_request=AsyncMock(),
            send_image_start=AsyncMock(),
            send_novelai_translation_start=AsyncMock(),
            send=AsyncMock(),
        )
        old_image = SimpleNamespace(run=AsyncMock())
        old_quick = SimpleNamespace(run=AsyncMock())
        v5_image = SimpleNamespace(run=AsyncMock(return_value=ConversationResult("ok", "v5", "v5")))
        v5_quick = SimpleNamespace(run=AsyncMock(return_value=ConversationResult("ok", "v5", "v5")))
        router = ExplicitCommandRouter(
            reply_sender=sender,
            gpt_image_workflow=old_image,
            novelai_image_workflow=old_image,
            novelai_quick_image_workflow=old_quick,
            novelai_prompt_workflow=SimpleNamespace(),
            novelai_style_workflow=SimpleNamespace(),
            nai_v5_image_workflow=v5_image,
            nai_v5_quick_image_workflow=v5_quick,
        )
        message = IncomingGroupMessage("group", "message", "member", "")
        for command in ("/Nai v5 作图 white cat", "Nai v5 作图 一只白猫"):
            parsed = parse_command(command)
            self.assertEqual(parsed.name, "nai_v5_image")
            self.assertTrue(await router.handle(parsed, message, first_msg_seq=1))

        self.assertEqual(v5_image.run.await_count, 2)
        v5_quick.run.assert_not_awaited()
        old_image.run.assert_not_awaited()
        old_quick.run.assert_not_awaited()
        sender.send_novelai_translation_start.assert_not_awaited()
        self.assertEqual(sender.send.await_count, 2)
        self.assertEqual(sender.acknowledge_image_request.await_count, 2)
        sender.send_image_start.assert_not_awaited()
        self.assertTrue(all(call.kwargs["show_generation_prompt"] for call in sender.send.await_args_list))


class NaiV5ConfigTests(unittest.TestCase):
    def test_v5_uses_its_own_token_and_default_endpoint(self):
        config = AppConfig.from_env({
            "QQ_BOT_APPID": "app", "QQ_BOT_SECRET": "secret",
            "OPENAI_API_KEY": "key", "NOVELAI_MCP_TOKEN": "old-token",
        })
        self.assertEqual(config.images.nai_v5_mcp_url, "https://www.fsstudy.com.cn/novel-ai-v5")
        self.assertEqual(config.images.nai_v5_mcp_token, "")
        self.assertEqual(config.images.nai_v5_daily_limit, 10)
        self.assertEqual(config.images.nai_v5_whitelist, frozenset())
        config = AppConfig.from_env({
            "QQ_BOT_APPID": "app", "QQ_BOT_SECRET": "secret",
            "OPENAI_API_KEY": "key", "NAI_V5_MCP_TOKEN": "new-token",
        })
        self.assertEqual(config.images.nai_v5_mcp_token, "new-token")

    def test_v5_limit_and_whitelist_are_configurable(self):
        base = {
            "QQ_BOT_APPID": "app", "QQ_BOT_SECRET": "secret",
            "OPENAI_API_KEY": "key",
        }
        config = AppConfig.from_env({
            **base,
            "AIQQ_NAI_V5_DAILY_LIMIT": "25",
            "AIQQ_NAI_V5_WHITELIST": "member-one, member-two",
        })
        self.assertEqual(config.images.nai_v5_daily_limit, 25)
        self.assertEqual(
            config.images.nai_v5_whitelist,
            frozenset({"member-one", "member-two"}),
        )
        for invalid in (
            {"AIQQ_NAI_V5_DAILY_LIMIT": "0"},
            {"AIQQ_NAI_V5_WHITELIST": "member-one,,member-two"},
        ):
            with self.assertRaises(ConfigurationError):
                AppConfig.from_env({**base, **invalid})


class NaiV5BootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_style_commands_keep_each_providers_generated_prompt_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig.from_env({
                "QQ_BOT_APPID": "app", "QQ_BOT_SECRET": "secret",
                "OPENAI_API_KEY": "key",
                "AIQQ_GROUP_MESSAGE_DB": f"{directory}/groups.db",
                "AIQQ_STATE_DB": f"{directory}/state.db",
                "AIQQ_MEDIA_DIR": f"{directory}/media",
                "AIQQ_REPLY_DIR": f"{directory}/replies",
                "AIQQ_LOG_FILE": f"{directory}/aiqq.log",
            })
            components = build_application(config).client._components
            router = components.message_handler._command_handler
            replies = SimpleNamespace(
                send=AsyncMock(), send_image_start=AsyncMock(), acknowledge_image_request=AsyncMock()
            )
            router._reply_sender = replies
            legacy = router._novelai_image_workflow
            v5 = router._nai_v5_image_workflow
            image = ImageAsset(b"image", "image/png", 1, 1)
            legacy._image_service.generate = AsyncMock(return_value=image)
            v5._image_service.generate = AsyncMock(return_value=image)
            auditor = legacy._prompt_auditor.audit = AsyncMock(
                return_value=ImagePromptAuditResult(
                    True, True, "safe", "safe scene", "", "square"
                )
            )
            message = IncomingGroupMessage("group", "message", "member", "")
            scene = "一只白猫，坐在窗边。"
            try:
                for initialize in components.initializers:
                    await initialize()

                async def dispatch(content, incoming=message):
                    parsed = parse_command(content)
                    self.assertIsNotNone(parsed)
                    self.assertTrue(await router.handle(parsed, incoming, first_msg_seq=1))
                    self.assertEqual(replies.send.await_args.args[0].status, "ok")

                await dispatch("/NovelAI画风 watercolor")
                await dispatch("/Nai v5 画风 {{pixel art}},\nsoft lighting")
                auditor.assert_not_awaited()
                legacy._image_service.generate.assert_not_awaited()
                v5._image_service.generate.assert_not_awaited()
                replies.send_image_start.assert_not_awaited()

                await dispatch("/Nai v5 画风")
                await dispatch(f"/Nai v5 作图 {scene}")
                await dispatch("/NovelAI生图 white cat")
                await dispatch("/Nai v5 画风 pixel art")
                await dispatch(f"/Nai v5 作图 {scene}")
                await dispatch("/NovelAI画风 oil painting")
                await dispatch(f"/Nai v5 作图 {scene}")
                await dispatch("/NovelAI画风")
                await dispatch(f"/Nai v5 作图 {scene}")
                await dispatch("/NovelAI生图 white cat")
                await dispatch("/NovelAI画风 watercolor")
                await dispatch("/Nai v5 画风")
                await dispatch("/NovelAI生图 white cat")
                await dispatch(f"/Nai v5 作图 {scene}")

                other_group = IncomingGroupMessage("second-group", "m2", "member", "")
                await dispatch("/Nai v5 画风 ink drawing", other_group)
                await dispatch(f"/Nai v5 作图 {scene}")
                other_member = IncomingGroupMessage("group", "m3", "other-member", "")
                await dispatch(f"/Nai v5 作图 {scene}", other_member)

                self.assertEqual(
                    [call.args[0] for call in legacy._image_service.generate.await_args_list],
                    ["watercolor, white cat", "white cat", "watercolor, white cat"],
                )
                self.assertEqual(
                    [call.args[0] for call in v5._image_service.generate.await_args_list],
                    [scene, *[f"pixel art, {scene}"] * 3, scene, f"ink drawing, {scene}", scene],
                )
                for call in auditor.await_args_list:
                    self.assertEqual(call.kwargs["require_english"], call.args[0].isascii())
                self.assertEqual(
                    len(replies.acknowledge_image_request.await_args_list), auditor.await_count
                )
                replies.send_image_start.assert_not_awaited()
            finally:
                for close in components.closers:
                    await close()

    async def test_new_token_file_wires_only_v5_service_and_health(self):
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory, "v5-token")
            token_file.write_text("v5-secret\n", encoding="utf-8")
            token_file.chmod(0o600)
            config = AppConfig.from_env({
                "QQ_BOT_APPID": "app", "QQ_BOT_SECRET": "secret",
                "OPENAI_API_KEY": "key",
                "NAI_V5_MCP_TOKEN_FILE": str(token_file),
                "AIQQ_GROUP_MESSAGE_DB": f"{directory}/groups.db",
                "AIQQ_STATE_DB": f"{directory}/state.db",
                "AIQQ_MEDIA_DIR": f"{directory}/media",
                "AIQQ_REPLY_DIR": f"{directory}/replies",
                "AIQQ_LOG_FILE": f"{directory}/aiqq.log",
            })
            application = build_application(config)
            components = application.client._components
            try:
                router = components.message_handler._command_handler
                service = router._nai_v5_image_workflow._image_service
                tag_client = (
                    router._nai_v5_quick_image_workflow
                    ._prompt_workflow._prompt_agent._tag_search._client
                )
                self.assertIs(tag_client, service._client)
                self.assertEqual(service._client._token, "v5-secret")
                self.assertFalse(router._novelai_image_workflow._image_service.is_configured)
                service._client.list_tools = AsyncMock(return_value={
                    "tools": FakeV5Client().tools,
                })
                await service.initialize()
                self.assertTrue(service.is_ready)
                health_route = next(
                    route for route in components.web_application.router.routes()
                    if route.method == "GET" and route.resource.canonical == "/health"
                )
                response = await health_route.handler(None)
                self.assertEqual(json.loads(response.text)["nai_v5"],
                                 {"configured": True, "ready": True})
                self.assertNotIn("v5-secret", response.text)
            finally:
                for close in components.closers:
                    await close()


if __name__ == "__main__":
    unittest.main()
