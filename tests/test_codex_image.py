import base64
import unittest
from io import BytesIO

from PIL import Image

from codex_image import CodexImageError, decode_codex_image


def image_bytes(image_format: str = "PNG") -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 24), (30, 120, 220)).save(output, format=image_format)
    return output.getvalue()


class CodexImageTests(unittest.TestCase):
    def test_valid_png_is_decoded_and_typed(self):
        data = image_bytes()

        image = decode_codex_image(base64.b64encode(data).decode("ascii"))

        self.assertEqual(image.data, data)
        self.assertEqual(image.mime_type, "image/png")

    def test_data_uri_is_supported(self):
        encoded = base64.b64encode(image_bytes("JPEG")).decode("ascii")

        image = decode_codex_image(f"data:image/jpeg;base64,{encoded}")

        self.assertEqual(image.mime_type, "image/jpeg")

    def test_invalid_base64_is_rejected(self):
        with self.assertRaises(CodexImageError):
            decode_codex_image("not-base64!")

    def test_non_image_data_is_rejected(self):
        encoded = base64.b64encode(b"not an image").decode("ascii")

        with self.assertRaises(CodexImageError):
            decode_codex_image(encoded)


if __name__ == "__main__":
    unittest.main()
