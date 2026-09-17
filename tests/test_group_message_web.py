import base64
import tempfile
import unittest
from types import SimpleNamespace

from group_message_store import GroupMessageStore
from group_message_web import GroupMessageViewer


def request(*, authorization="", query=None):
    return SimpleNamespace(
        headers={"Authorization": authorization} if authorization else {},
        rel_url=SimpleNamespace(query=query or {}),
    )


def basic(username="admin", password="secret"):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


def gateway_event(
    message_id: str,
    *,
    group_openid: str = "group-1",
    username: str = "小明",
    content: str = "普通消息",
):
    return {
        "id": f"event-{message_id}",
        "d": {
            "id": message_id,
            "group_openid": group_openid,
            "content": content,
            "message_type": 0,
            "timestamp": "2026-09-08T15:00:00+08:00",
            "author": {
                "member_openid": "member-1",
                "username": username,
                "member_role": "member",
            },
            "attachments": [
                {
                    "content_type": "image/jpeg",
                    "filename": "photo.jpg",
                    "url": "https://example.com/photo.jpg?token=temporary",
                }
            ],
            "msg_elements": [{"content": "被引用的消息"}],
            "message_scene": {"ext": ["auth_token=must-not-render"]},
        },
    }


class GroupMessageViewerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = GroupMessageStore(
            f"{self.temp_dir.name}/group_messages.db"
        )
        await self.store.initialize()
        self.viewer = GroupMessageViewer(
            self.store,
            username="admin",
            password="secret",
        )

    async def asyncTearDown(self):
        await self.store.close()
        self.temp_dir.cleanup()

    async def test_missing_or_wrong_credentials_are_rejected(self):
        missing = await self.viewer.serve(request())
        wrong = await self.viewer.serve(
            request(authorization=basic(password="wrong"))
        )

        self.assertEqual(missing.status, 401)
        self.assertEqual(wrong.status, 401)
        self.assertIn("Basic", missing.headers["WWW-Authenticate"])
        self.assertEqual(missing.headers["Cache-Control"], "private, no-store")

    async def test_unconfigured_viewer_fails_closed(self):
        viewer = GroupMessageViewer(
            self.store,
            username="admin",
            password="",
        )

        response = await viewer.serve(request(authorization=basic()))

        self.assertEqual(response.status, 503)

    async def test_authorized_page_lists_groups_messages_and_attachments(self):
        await self.store.save_gateway_event(
            "GROUP_MESSAGE_CREATE", gateway_event("message-1")
        )
        await self.store.save_gateway_event(
            "GROUP_MESSAGE_CREATE",
            gateway_event(
                "message-2",
                group_openid="group-2",
                username='<script>alert("x")</script>',
                content="第二个群",
            ),
        )

        response = await self.viewer.serve(
            request(
                authorization=basic(),
                query={"group": "group-1"},
            )
        )

        self.assertEqual(response.status, 200)
        self.assertIn("AiQQ 群消息", response.text)
        self.assertIn("普通消息", response.text)
        self.assertIn("被引用的消息", response.text)
        self.assertIn("photo.jpg", response.text)
        self.assertIn("group-2", response.text)
        self.assertNotIn("<script>alert", response.text)
        self.assertNotIn("must-not-render", response.text)
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    async def test_unknown_group_falls_back_to_latest_group(self):
        await self.store.save_gateway_event(
            "GROUP_MESSAGE_CREATE", gateway_event("message-1")
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
