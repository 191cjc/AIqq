import asyncio
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image

from aiqq.services.chat_read import GroupChatReadService
from aiqq.database.connection import SQLiteConnection
from aiqq.database.group_messages import GroupMessageRepository
from aiqq.services.images.chat_decode import ChatImageDecodeError, decode_chat_image
from aiqq.services.images.web import WebImageError, WebImageService
from tests.unit.services.test_web_image import FakeResponse, FakeSession


def image_bytes(format="PNG", colors=("red",)):
    output = BytesIO()
    frames = [Image.new("RGB", (20, 16), color) for color in colors]
    options = {"save_all": True, "append_images": frames[1:], "duration": 80} if len(frames) > 1 else {}
    frames[0].save(output, format=format, **options)
    return output.getvalue()


class DecoderTests(unittest.TestCase):
    def test_static_and_animated_inputs_produce_decodable_visual_frames(self):
        colors = ("red", "green", "blue", "white", "yellow")
        for format in ("GIF", "WEBP"):
            with self.subTest(format=format):
                result = decode_chat_image(image_bytes(format, colors))
                self.assertEqual(len(result.images), 3)
                self.assertEqual(result.metadata["frame_count"], 5)
                self.assertTrue(result.metadata["sampled"])
                self.assertEqual([frame["frame_index"] for frame in result.metadata["frames"]], [0, 2, 4])
                for frame, expected in zip(result.images, ((255, 0, 0), (0, 0, 255), (255, 255, 0))):
                    with Image.open(BytesIO(frame.data)) as decoded:
                        pixel = decoded.getpixel((5, 5))
                        self.assertLess(max(abs(a - b) for a, b in zip(pixel, expected)), 12)
        for format in ("JPEG", "PNG", "WEBP", "GIF"):
            result = decode_chat_image(image_bytes(format))
            self.assertEqual(len(result.images), 1)
            self.assertFalse(result.metadata["animated"])
            self.assertEqual(result.metadata["size"], len(image_bytes(format)))
            self.assertEqual(len(result.metadata["sha256"]), 64)

    def test_animation_and_pixel_limits_are_enforced(self):
        data = image_bytes("GIF", ("red", "green", "blue"))
        for constant, value in (("MAX_ANIMATION_FRAMES", 2), ("MAX_ANIMATION_PIXELS", 500), ("MAX_IMAGE_PIXELS", 100)):
            with self.subTest(constant=constant), patch("aiqq.services.images.chat_decode." + constant, value):
                with self.assertRaises(ChatImageDecodeError) as caught:
                    decode_chat_image(data)
                self.assertEqual(caught.exception.kind, "too_large")

    def test_invalid_and_unsupported_images_are_not_visual_inputs(self):
        for data in (b"garbage", image_bytes("BMP")):
            with self.assertRaises(ChatImageDecodeError) as caught:
                decode_chat_image(data)
            self.assertEqual(caught.exception.kind, "invalid_image")


class ReadDownloaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_gif_is_readable_without_weakening_generation_validation(self):
        data = image_bytes("GIF", ("red", "blue"))
        service = WebImageService()
        responses = FakeSession([FakeResponse(body=data, content_type="image/gif"), FakeResponse(body=data, content_type="image/gif")])
        with patch.object(service, "_get_session", return_value=responses):
            decoded = await service.download_for_read("https://example.com/a.gif")
            self.assertEqual(len(decoded.images), 2)
            with self.assertRaises(WebImageError) as caught:
                await service.download("https://example.com/a.gif")
        self.assertEqual(caught.exception.kind, "invalid_image")

    async def test_read_uses_existing_url_redirect_and_error_boundaries(self):
        for status, kind in ((404, "not_found"), (410, "not_found"), (400, "download_failed"), (403, "access_denied")):
            service = WebImageService()
            with patch.object(service, "_get_session", return_value=FakeSession([FakeResponse(status=status)])):
                with self.assertRaises(WebImageError) as caught:
                    await service.download_for_read("https://example.com/a.gif")
                self.assertEqual(caught.exception.kind, kind)
        service = WebImageService()
        with patch.object(service, "_get_session", return_value=FakeSession([FakeResponse(status=302, location="https://127.0.0.1/private")])):
            with self.assertRaises(WebImageError):
                await service.download_for_read("https://example.com/a.gif")


class FakeRepository:
    def __init__(self):
        self.calls = []
        self.reads = []
        self.records = {
            index: {"record": {"record_id": index, "recalled_at": ""},
                    "payload": {"unknown": [None, False, 0], "content": "正文\n  " * 5000},
                    "derived": {"has_image": True, "recall_state": "unobserved"}}
            for index in range(1, 7)
        }
        self.records[5]["record"]["recalled_at"] = "2026-09-16"

    async def query_history(self, group_id, **filters):
        self.calls.append((group_id, filters))
        return {"messages": list(self.records.values()), "has_more": True, "next_cursor": 1}

    async def get_full_record(self, group_id, record_id, version_id=None):
        self.calls.append((group_id, record_id))
        return self.records.get(record_id) if group_id == "group-a" else None

    async def get_image_attachment(self, group_id, record_id, attachment_index=0, version_id=None):
        self.calls.append((group_id, record_id, attachment_index, version_id))
        if attachment_index > 2:
            return None
        return {"attachment_id": record_id * 10 + attachment_index, "version_id": version_id or 1,
                "url": "https://example.com/source", "path": "/attachments/0"}

    async def record_image_read(self, group_id, record_id, **fields):
        self.reads.append((group_id, record_id, fields))
        return True


class ChatReadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repository = FakeRepository()
        self.downloader = type("Downloader", (), {"download_for_read": AsyncMock(return_value=decode_chat_image(image_bytes()))})()
        self.service = GroupChatReadService(repository=self.repository, downloader=self.downloader)
        self.session = self.service.create_session("group-a")

    async def asyncTearDown(self):
        await self.session.close()

    async def test_full_history_group_binding_paging_and_image_ids(self):
        for _ in range(3):
            result = await self.session.read_history(before_record_id=30, limit=50, keyword="正文")
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["messages"][0], self.repository.records[1])
            self.assertGreater(len(result["messages"][0]["payload"]["content"]), 10000)
            self.assertTrue(result["has_more"])
        self.assertEqual(self.session.image_record_ids, frozenset({1, 2, 3, 4, 6}))
        blocked = await self.session.read_history()
        self.assertEqual(blocked["error_kind"], "budget_exhausted")
        self.assertEqual(len(blocked["ranges_read"]), 3)
        self.assertTrue(all(call[0] == "group-a" for call in self.repository.calls))
        self.downloader.download_for_read.assert_not_awaited()

    async def test_history_rejects_other_groups_paths_and_invalid_limits(self):
        for filters in ({"group_id": "other"}, {"database": "/secret"}, {"limit": 51}, {"limit": True}, {"record_id": -1}):
            self.assertEqual((await self.session.read_history(**filters))["error_kind"], "invalid_request")
        self.assertEqual(self.repository.calls, [])

    async def test_distinct_source_budget_and_request_only_reuse(self):
        for record_id in (1, 2, 3, 1):
            result, images = await self.session.read_image(record_id)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(len(images), 1)
        self.assertEqual(self.downloader.download_for_read.await_count, 3)
        result, images = await self.session.read_image(4)
        self.assertEqual(result["error_kind"], "budget_exhausted")
        self.assertEqual(images, ())
        self.assertEqual(len(self.repository.reads), 3)
        metadata = self.repository.reads[0][2]["metadata"]
        self.assertEqual(metadata["measurement_source"], "on_demand_download")
        self.assertFalse(any(isinstance(value, bytes) for value in metadata.values()))
        second = self.service.create_session("group-a")
        await second.read_image(1)
        await second.close()
        self.assertEqual(self.downloader.download_for_read.await_count, 4)

    async def test_recalled_missing_and_cross_group_do_not_download(self):
        for record_id, expected in ((5, "recalled"), (999, "missing")):
            result, images = await self.session.read_image(record_id)
            self.assertEqual(result["error_kind"], expected)
            self.assertEqual(images, ())
        other = self.service.create_session("other")
        self.assertEqual((await other.read_image(1))[0]["error_kind"], "missing")
        await other.close()
        self.downloader.download_for_read.assert_not_awaited()

    async def test_error_categories_preserve_expiry_distinction_and_metadata(self):
        for kind, status, expected in (("not_found", 410, "expired"), ("download_failed", 400, "download_failed"),
                                       ("access_denied", 403, "access_denied"), ("timeout", None, "timeout")):
            session = self.service.create_session("group-a")
            self.downloader.download_for_read.side_effect = WebImageError("private diagnostic", kind=kind, status_code=status)
            result, images = await session.read_image(1)
            self.assertEqual(result["error_kind"], expected)
            self.assertFalse(result["pixels_available"])
            self.assertEqual(result["http_status"], status)
            self.assertEqual(images, ())
            self.assertNotIn("private", json.dumps(result))
            if expected == "expired":
                self.assertEqual(result["message"], "这张图片链接已过期或失效，请重新发送图片。")
            else:
                self.assertNotIn("过期", result["message"])
            self.assertIsNone(self.repository.reads[-1][2]["metadata"])
            await session.close()

    async def test_close_cancels_download_and_drops_per_request_frames(self):
        started = asyncio.Event()
        async def download(_url):
            started.set()
            await asyncio.Future()
        self.downloader.download_for_read.side_effect = download
        task = asyncio.create_task(self.session.read_image(1))
        await started.wait()
        await self.session.close()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.repository.reads, [])
        self.assertEqual((await self.session.read_image(1))[0]["error_kind"], "closed")
        self.assertEqual(self.session.retrieved_record_ids, frozenset())

    async def test_version_and_attachment_index_are_forwarded_without_url_override(self):
        result, _ = await self.session.read_image(1, attachment_index=2, version_id=9)
        self.assertEqual(result["version_id"], 9)
        self.assertIn(("group-a", 1, 2, 9), self.repository.calls)
        for args in ((True, 0), (1, -1), (1, True)):
            self.assertEqual((await self.session.read_image(*args))[0]["error_kind"], "invalid_request")


class SkillScriptTests(unittest.TestCase):
    def test_actual_scripts_send_scoped_json_and_preserve_full_result(self):
        requests = []
        full_result = {"status": "ok", "messages": [{"payload": {"long": "正文\n" * 6000, "unknown": [None, False, 0]}}], "images": [{"path": "/work/read.jpg"}]}
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append((self.path, self.headers.get("Authorization"), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                body = json.dumps(full_result, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        scripts = Path(__file__).resolve().parents[3] / ".agents/skills/aiqq-chat-read/scripts"
        try:
            env = {"AIQQ_CHAT_READ_URL": f"http://127.0.0.1:{server.server_port}", "AIQQ_CHAT_READ_TOKEN": "turn-secret", "PYTHONUTF8": "1"}
            for script, args in (("read_history.py", ["--before-record-id", "50", "--limit", "50", "--events"]),
                                 ("read_image.py", ["--record-id", "21", "--attachment-index", "2", "--version-id", "9"])):
                result = subprocess.run([sys.executable, str(scripts / script), *args], env=env, capture_output=True, text=True, timeout=5, check=True)
                self.assertEqual(json.loads(result.stdout), full_result)
                self.assertNotIn("turn-secret", result.stdout + result.stderr)
            self.assertEqual(requests[0], ("/history", "Bearer turn-secret", {"before_record_id": 50, "limit": 50, "events": True}))
            self.assertEqual(requests[1][2], {"record_id": 21, "attachment_index": 2, "version_id": 9})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


class StorageReadIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_attachment_version_read_metadata_and_group_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = GroupMessageRepository(SQLiteConnection(Path(directory) / "history.db"))
            downloader = type("Downloader", (), {"download_for_read": AsyncMock(return_value=decode_chat_image(image_bytes("GIF", ("red", "blue"))))})()
            service = GroupChatReadService(repository=repository, downloader=downloader)
            session = service.create_session("group-a")
            other = service.create_session("group-b")
            try:
                await repository.add_gateway_event("GROUP_MESSAGE_CREATE", {
                    "op": 0, "s": 51, "t": "GROUP_MESSAGE_CREATE", "id": "event-1",
                    "d": {"id": "message-1", "group_openid": "group-a", "content": "看一下图片\n",
                          "timestamp": "2026-09-16T00:00:00+00:00", "author": {"member_openid": "member"},
                          "attachments": [{"content_type": "text/plain", "url": "https://example.com/text"},
                                          {"content_type": "image/gif", "url": "https://example.com/a.gif", "future": [None, False, 0]}]},
                })
                page = await session.read_history()
                wire = page["messages"][0]
                record_id = wire["record"]["record_id"]
                self.assertEqual(wire["attachments"][1]["image_attachment_index"], 0)
                result, images = await session.read_image(record_id, version_id=wire["attachments"][1]["version_id"])
                self.assertEqual(result["status"], "ok")
                self.assertEqual(len(images), 2)
                refreshed = await repository.get_full_record("group-a", record_id)
                attachment = refreshed["attachments"][1]
                self.assertEqual(attachment["last_read_status"], "ok")
                self.assertEqual(attachment["metadata"]["future"], [None, False, 0])
                self.assertEqual(json.loads(attachment["measured_metadata_json"])["frame_count"], 2)
                self.assertEqual((await other.read_image(record_id))[0]["error_kind"], "missing")
                self.assertEqual((await other.read_history())["messages"], [])
                self.assertEqual(downloader.download_for_read.await_count, 1)
                # No historical originals are written by any stage of the read.
                self.assertTrue(all(path.name.startswith("history.db") for path in Path(directory).iterdir()))
            finally:
                await session.close()
                await other.close()
                await repository.close()


if __name__ == "__main__":
    unittest.main()
