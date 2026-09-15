import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aiqq.database import GroupMessageRepository, SQLiteConnection
from aiqq.interfaces.web.group_messages import GroupMessageViewer


def basic(username="admin", password="secret"):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


def request(*, authorization="", query=None):
    async def post():
        return {}

    return SimpleNamespace(
        headers={"Authorization": authorization} if authorization else {},
        rel_url=SimpleNamespace(query=query or {}),
        post=post,
    )


def post_request(*, authorization="", form=None):
    async def post():
        return form or {}

    return SimpleNamespace(
        headers={"Authorization": authorization} if authorization else {},
        rel_url=SimpleNamespace(query={}),
        post=post,
    )


def event(message_id, *, group_id="group-1", username="小明", content="普通消息"):
    return {
        "id": f"event-{message_id}",
        "d": {
            "id": message_id,
            "group_openid": group_id,
            "content": content,
            "message_type": 0,
            "timestamp": "2026-09-09T12:00:00+08:00",
            "author": {
                "member_openid": "member-1",
                "username": username,
                "member_role": "member",
            },
            "attachments": [
                {
                    "content_type": "image/jpeg",
                    "filename": "photo.jpg",
                    "url": "https://images.example/photo.jpg",
                }
            ],
            "msg_elements": [{"content": "被引用的消息"}],
            "message_scene": {"ext": ["secret=must-not-render"]},
        },
    }


class FakeSender:
    def __init__(self):
        self.calls = []

    async def send_proactive_text(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(message_id="bot-message")


class GroupMessageViewerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repository = GroupMessageRepository(
            SQLiteConnection(Path(self.temporary.name) / "messages.db")
        )
        await self.repository.initialize()
        self.sender = FakeSender()
        self.viewer = GroupMessageViewer(
            self.repository,
            self.sender,
            username="admin",
            password="secret",
        )

    async def asyncTearDown(self):
        await self.repository.close()
        self.temporary.cleanup()

    async def test_authentication_fails_closed(self):
        missing = await self.viewer.serve(request())
        wrong = await self.viewer.serve(
            request(authorization=basic(password="wrong"))
        )
        unconfigured = await GroupMessageViewer(
            self.repository,
            self.sender,
            username="admin",
            password="",
        ).serve(request(authorization=basic()))

        self.assertEqual(missing.status, 401)
        self.assertEqual(wrong.status, 401)
        self.assertEqual(unconfigured.status, 503)
        self.assertIn("Basic", missing.headers["WWW-Authenticate"])
        self.assertEqual(missing.headers["Cache-Control"], "private, no-store")

    async def test_page_renders_allowlisted_message_fields_and_escapes_html(self):
        await self.repository.add_gateway_event(
            "GROUP_MESSAGE_CREATE", event("m1")
        )
        await self.repository.add_gateway_event(
            "GROUP_MESSAGE_CREATE",
            event(
                "m2",
                group_id="group-2",
                username='<script>alert("x")</script>',
                content="第二个群",
            ),
        )

        response = await self.viewer.serve(
            request(authorization=basic(), query={"group": "group-1"})
        )

        self.assertEqual(response.status, 200)
        self.assertIn("AiQQ 群消息", response.text)
        self.assertIn("普通消息", response.text)
        self.assertIn("被引用的消息", response.text)
        self.assertIn("photo.jpg", response.text)
        self.assertIn("group-2", response.text)
        self.assertNotIn("<script>alert", response.text)
        self.assertNotIn("must-not-render", response.text)
        self.assertIn('class="composer-wrap"', response.text)
        self.assertIn('name="content"', response.text)
        self.assertIn('name="csrf_token"', response.text)
        self.assertIn('maxlength="2000"', response.text)
        self.assertIn(
            "frame-ancestors 'none'",
            response.headers["Content-Security-Policy"],
        )

    async def test_authenticated_post_sends_to_selected_group(self):
        await self.repository.add_gateway_event(
            "GROUP_MESSAGE_CREATE", event("m1")
        )

        response = await self.viewer.send_message(
            post_request(
                authorization=basic(),
                form={
                    "csrf_token": self.viewer._csrf_token,
                    "group_openid": "group-1",
                    "content": "  后台发送  ",
                },
            )
        )

        self.assertEqual(response.status, 303)
        self.assertEqual(
            self.sender.calls,
            [
                {
                    "group_id": "group-1",
                    "content": "后台发送",
                    "origin": "message_viewer",
                }
            ],
        )
        self.assertIn("group=group-1", response.headers["Location"])
        self.assertIn("send=sent", response.headers["Location"])

    async def test_post_rejects_invalid_csrf_token(self):
        response = await self.viewer.send_message(
            post_request(
                authorization=basic(),
                form={
                    "csrf_token": "wrong",
                    "group_openid": "group-1",
                    "content": "后台发送",
                },
            )
        )

        self.assertEqual(response.status, 403)
        self.assertEqual(self.sender.calls, [])

    async def test_unknown_group_falls_back_to_latest_group(self):
        await self.repository.add_gateway_event(
            "GROUP_MESSAGE_CREATE", event("m1")
        )
        response = await self.viewer.serve(
            request(
                authorization=basic(),
                query={"group": "does-not-exist"},
            )
        )
        self.assertEqual(response.status, 200)
        self.assertIn("普通消息", response.text)
        self.assertNotIn("does-not-exist", response.text)


if __name__ == "__main__":
    unittest.main()
