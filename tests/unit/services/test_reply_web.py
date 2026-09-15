import unittest

from aiqq.interfaces.web.replies import render_reply_page


class ReplyWebTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
