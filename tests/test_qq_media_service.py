import unittest
from types import SimpleNamespace

from qq_media_service import QQMediaError, upload_qq_image


class FakeAPI:
    def __init__(self, result=None):
        self.result = result or {"file_info": "file-info"}
        self.group_calls = []
        self.c2c_calls = []

    async def post_group_file(self, **kwargs):
        self.group_calls.append(kwargs)
        return self.result

    async def post_c2c_file(self, **kwargs):
        self.c2c_calls.append(kwargs)
        return self.result


class QQMediaServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_image_is_uploaded_without_direct_send(self):
        api = FakeAPI()
        message = SimpleNamespace(_api=api, group_openid="group-1")

        media = await upload_qq_image(message, "https://example.com/image.png")

        self.assertEqual(media, {"file_info": "file-info"})
        self.assertEqual(
            api.group_calls,
            [
                {
                    "group_openid": "group-1",
                    "file_type": 1,
                    "url": "https://example.com/image.png",
                    "srv_send_msg": False,
                }
            ],
        )
        self.assertEqual(api.c2c_calls, [])

    async def test_c2c_image_is_uploaded_to_message_author(self):
        api = FakeAPI()
        message = SimpleNamespace(
            _api=api,
            author=SimpleNamespace(user_openid="user-1"),
        )

        media = await upload_qq_image(message, "https://example.com/image.png")

        self.assertEqual(media, {"file_info": "file-info"})
        self.assertEqual(
            api.c2c_calls,
            [
                {
                    "openid": "user-1",
                    "file_type": 1,
                    "url": "https://example.com/image.png",
                    "srv_send_msg": False,
                }
            ],
        )
        self.assertEqual(api.group_calls, [])

    async def test_missing_file_info_is_rejected(self):
        api = FakeAPI({"file_uuid": "uuid"})
        message = SimpleNamespace(_api=api, group_openid="group-1")

        with self.assertRaises(QQMediaError):
            await upload_qq_image(message, "https://example.com/image.png")


if __name__ == "__main__":
    unittest.main()
