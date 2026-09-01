import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web

from reply_store import TemporaryReplyStore


class TemporaryReplyStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = TemporaryReplyStore(
            self.temp_dir.name,
            "https://example.com/aiqq",
            3600,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_save_creates_private_file_and_random_public_url(self):
        await self.store.initialize()

        first = self.store.save("第一份完整原文")
        second = self.store.save("第二份完整原文")

        self.assertNotEqual(first.file_name, second.file_name)
        self.assertEqual(
            first.public_url,
            f"https://example.com/aiqq/reply/{first.file_name}",
        )
        path = Path(self.temp_dir.name) / first.file_name
        self.assertEqual(path.read_text(encoding="utf-8"), "第一份完整原文")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    async def test_serve_renders_markdown_and_escapes_raw_html(self):
        await self.store.initialize()
        export = self.store.save(
            "# 标题\n\n**重点**\n\n- 第一项\n- 第二项\n\n"
            "```python\nprint('ok')\n```\n\n<script>alert('x')</script>"
        )
        request = SimpleNamespace(match_info={"file_name": export.file_name})

        response = await self.store.serve(request)

        self.assertEqual(response.status, 200)
        page = response.text
        self.assertIn("复制完整原文", page)
        self.assertIn("<h1>标题</h1>", page)
        self.assertIn("<strong>重点</strong>", page)
        self.assertIn("<li>第一项</li>", page)
        self.assertIn("<code class=\"language-python\">", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>alert('x')</script>", page)

    async def test_copy_button_uses_unmodified_markdown_source(self):
        await self.store.initialize()
        export = self.store.save("# 标题\n\n**重点**")
        request = SimpleNamespace(match_info={"file_name": export.file_name})

        response = await self.store.serve(request)

        page = response.text
        self.assertIn('<textarea id="raw"', page)
        self.assertIn("# 标题\n\n**重点**</textarea>", page)
        self.assertIn("navigator.clipboard.writeText(raw.value)", page)

    async def test_page_includes_security_headers(self):
        await self.store.initialize()
        export = self.store.save("安全内容")
        request = SimpleNamespace(match_info={"file_name": export.file_name})

        response = await self.store.serve(request)

        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        self.assertIn("default-src 'none'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("noindex", response.headers["X-Robots-Tag"])

    async def test_unsafe_markdown_link_is_neutralized(self):
        await self.store.initialize()
        export = self.store.save("[危险链接](javascript:alert(1))")
        request = SimpleNamespace(match_info={"file_name": export.file_name})

        response = await self.store.serve(request)

        self.assertIn('href="#harmful-link"', response.text)
        self.assertNotIn('href="javascript:', response.text)

    async def test_invalid_file_name_returns_404(self):
        request = SimpleNamespace(match_info={"file_name": "../memory.db"})

        with self.assertRaises(web.HTTPNotFound):
            await self.store.serve(request)

    async def test_expired_reply_is_deleted_and_returns_404(self):
        store = TemporaryReplyStore(
            self.temp_dir.name,
            "https://example.com/aiqq",
            300,
        )
        await store.initialize()
        export = store.save("已经过期")
        path = Path(self.temp_dir.name) / export.file_name
        expired_time = time.time() - 301
        os.utime(path, (expired_time, expired_time))
        request = SimpleNamespace(match_info={"file_name": export.file_name})

        with self.assertRaises(web.HTTPNotFound):
            await store.serve(request)

        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
