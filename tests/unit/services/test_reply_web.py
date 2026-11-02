import unittest
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp import web

from aiqq.interfaces.web.replies import ReplyHandler, render_reply_page
from aiqq.services.storage.temporary_reply import ReplyDocument


class ReplyWebTests(unittest.TestCase):
    def test_prompt_view_renders_one_literal_copy_block_without_markdown_changes(self):
        content = '1girl, {blue eyes}, 3::long hair::\n# literal title\n```text\n</code><script>alert(1)</script>\n你好 & world'
        page = render_reply_page(content, prompt=True)

        class CodeCollector(HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_code = False
                self.parts = []

            def handle_starttag(self, tag, attrs):
                if tag == "code":
                    self.in_code = True

            def handle_endtag(self, tag):
                if tag == "code":
                    self.in_code = False

            def handle_data(self, data):
                if self.in_code:
                    self.parts.append(data)

        parser = CodeCollector()
        parser.feed(page)
        self.assertEqual("".join(parser.parts), content)
        self.assertEqual(page.count('aria-label="复制提示词"'), 1)
        self.assertIn("本次提示词", page)
        self.assertNotIn('<script>alert(1)</script>', page)
        self.assertNotIn('http-equiv="refresh"', page)

    def test_each_block_has_a_copy_button_without_whole_reply_copy(self):
        content = (
            "正文和 `inline code`。\n\n"
            "```python\nif True:\n    print('你好 & <world>')\n```\n\n"
            "```\nsecond block\n```\n\n"
            "    indented code\n"
        )
        page = render_reply_page(content)
        self.assertEqual(page.count('data-copy-code aria-label="复制代码"'), 3)
        self.assertIn('<span class="code-language">python</span>', page)
        self.assertEqual(page.count('<span class="code-language">代码</span>'), 2)
        self.assertIn("    print(&#x27;你好 &amp; &lt;world&gt;&#x27;)\n</code>", page)
        self.assertIn("<code>inline code</code>", page)
        self.assertNotIn("复制完整原文", page)
        self.assertNotIn('id="raw"', page)
        self.assertNotIn("<textarea", page)
        self.assertNotIn("<button", render_reply_page("正文和 `inline code`。"))

    def test_code_and_language_cannot_inject_html_or_event_attributes(self):
        class AttributeCollector(HTMLParser):
            def __init__(self):
                super().__init__()
                self.attributes = []

            def handle_starttag(self, tag, attrs):
                self.attributes.extend(attrs)

        page = render_reply_page(
            '```python" onclick="alert(1)\n'
            '</code><script>alert("unsafe")</script>\n```'
        )
        parser = AttributeCollector()
        parser.feed(page)
        self.assertFalse(any(name.startswith("on") for name, _ in parser.attributes))
        self.assertIn("&lt;/code&gt;&lt;script&gt;", page)
        self.assertNotIn('<script>alert("unsafe")</script>', page)

    def test_markdown_is_rendered_and_raw_content_is_html_escaped(self):
        page = render_reply_page("# Title\n\n<script>alert(1)</script>")
        self.assertIn("<h1>Title</h1>", page)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)

    def test_pending_page_refreshes_until_reply_is_complete(self):
        pending = render_reply_page("任务仍在处理中。", pending=True)
        completed = render_reply_page("任务已经完成。", pending=False)

        self.assertIn('http-equiv="refresh"', pending)
        self.assertIn("任务进展", pending)
        self.assertNotIn('http-equiv="refresh"', completed)
        self.assertIn("完整输出", completed)


class ReplyHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_reply_route_supports_literal_prompt_view(self):
        store = SimpleNamespace(load_document=AsyncMock(return_value=ReplyDocument("{white cat}, **literal**", False)))
        request = SimpleNamespace(match_info={"file_name": "synthetic.txt"}, query={"view": "prompt"})
        response = await ReplyHandler(store).serve(request)
        self.assertIn("本次提示词", response.text)
        self.assertIn("{white cat}, **literal**</code>", response.text)
        self.assertNotIn("<strong>literal</strong>", response.text)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        store.load_document.return_value = ReplyDocument("# Still processing", True)
        pending = await ReplyHandler(store).serve(request)
        self.assertIn('http-equiv="refresh"', pending.text)
        self.assertIn("任务进展", pending.text)

    async def test_reply_page_keeps_privacy_headers_and_expired_link_behavior(self):
        store = SimpleNamespace(load_document=AsyncMock(
            return_value=ReplyDocument("```text\nprivate code\n```", False)
        ))
        handler = ReplyHandler(store)
        request = SimpleNamespace(match_info={"file_name": "synthetic.txt"}, query={})
        response = await handler.serve(request)
        self.assertEqual(response.content_type, "text/html")
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow, noarchive")
        self.assertIn("default-src 'none'", response.headers["Content-Security-Policy"])
        self.assertIn("data-copy-code", response.text)
        store.load_document.return_value = None
        with self.assertRaises(web.HTTPNotFound):
            await handler.serve(request)


if __name__ == "__main__":
    unittest.main()
