import os
import unittest
from unittest.mock import patch

from bot import qq_http_timeout_seconds


class BotConfigTests(unittest.TestCase):
    def test_qq_http_timeout_defaults_to_30_seconds(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(qq_http_timeout_seconds(), 30)

    def test_qq_http_timeout_accepts_environment_override(self):
        with patch.dict(
            os.environ, {"AIQQ_QQ_HTTP_TIMEOUT_SECONDS": "45"}, clear=True
        ):
            self.assertEqual(qq_http_timeout_seconds(), 45)

    def test_qq_http_timeout_rejects_out_of_range_value(self):
        with patch.dict(
            os.environ, {"AIQQ_QQ_HTTP_TIMEOUT_SECONDS": "121"}, clear=True
        ):
            with self.assertRaises(SystemExit):
                qq_http_timeout_seconds()


if __name__ == "__main__":
    unittest.main()
