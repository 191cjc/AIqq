import random
import unittest
from io import BytesIO

from PIL import Image

from aiqq.logic.models import ImageAsset
from aiqq.services.images.qq_upload import MAX_QQ_IMAGE_BYTES, prepare_qq_image
from aiqq.services.images.validation import InvalidImage, validate_image


def encode(source, image_format="PNG", **options):
    output = BytesIO()
    source.save(output, format=image_format, **options)
    return validate_image(output.getvalue())


def noise_image(size):
    return Image.frombytes("RGB", size, random.Random(27).randbytes(size[0] * size[1] * 3))


class QQImageUploadTests(unittest.TestCase):
    def assert_jpeg(self, prepared):
        self.assertEqual(prepared.mime_type, "image/jpeg")
        self.assertLessEqual(len(prepared.data), MAX_QQ_IMAGE_BYTES)
        with Image.open(BytesIO(prepared.data)) as decoded:
            decoded.load()
            self.assertEqual(decoded.format, "JPEG")
            self.assertEqual(decoded.size, (prepared.width, prepared.height))
            self.assertEqual(decoded.mode, "RGB")

    def test_small_and_exact_limit_images_are_unchanged(self):
        with Image.new("RGB", (16, 8), "blue") as source:
            for image_format in ("PNG", "JPEG", "WEBP"):
                image = encode(source, image_format)
                for size in (len(image.data), MAX_QQ_IMAGE_BYTES - 1, MAX_QQ_IMAGE_BYTES):
                    with self.subTest(image_format=image_format, size=size):
                        padded = ImageAsset(
                            image.data.ljust(size, b"\0"), image.mime_type,
                            image.width, image.height,
                        )
                        self.assertIs(prepare_qq_image(padded), padded)

    def test_one_byte_over_limit_is_compressed(self):
        with Image.new("RGB", (16, 8), "blue") as source:
            image = encode(source)
        oversized = ImageAsset(
            image.data.ljust(MAX_QQ_IMAGE_BYTES + 1, b"\0"), "image/png", 16, 8,
        )

        prepared = prepare_qq_image(oversized)

        self.assert_jpeg(prepared)
        self.assertEqual((prepared.width, prepared.height), (16, 8))

    def test_large_png_jpeg_and_webp_become_valid_jpegs(self):
        with noise_image((1024, 1024)) as source:
            cases = (
                ("PNG", {}),
                ("JPEG", {"quality": 100, "subsampling": 0}),
                ("WEBP", {"lossless": True}),
            )
            for image_format, options in cases:
                with self.subTest(image_format=image_format):
                    image = encode(source, image_format, **options)
                    self.assertGreater(len(image.data), MAX_QQ_IMAGE_BYTES)

                    prepared = prepare_qq_image(image)

                    self.assert_jpeg(prepared)
                    self.assertEqual((prepared.width, prepared.height), source.size)

    def test_reduces_quality_before_resolution(self):
        with noise_image((1900, 1400)) as source:
            high_quality = encode(source, "JPEG", quality=90, optimize=True)
            self.assertGreater(len(high_quality.data), MAX_QQ_IMAGE_BYTES)
            image = encode(source)

        prepared = prepare_qq_image(image)

        self.assert_jpeg(prepared)
        self.assertEqual((prepared.width, prepared.height), (1900, 1400))

    def test_high_entropy_image_is_resized_proportionally_when_needed(self):
        with noise_image((3000, 2000)) as source:
            lowest_quality = encode(source, "JPEG", quality=70, optimize=True)
            self.assertGreater(len(lowest_quality.data), MAX_QQ_IMAGE_BYTES)
            image = encode(source)

        prepared = prepare_qq_image(image)

        self.assert_jpeg(prepared)
        self.assertLess(prepared.width, image.width)
        self.assertLess(prepared.height, image.height)
        self.assertAlmostEqual(prepared.width / prepared.height, 1.5, delta=0.002)

    def test_alpha_and_palette_transparency_are_composited_onto_white(self):
        cases = (("RGBA", (1024, 1024)), ("P", (2048, 1100)))
        for mode, size in cases:
            with self.subTest(mode=mode), Image.new(mode, size, 0) as source:
                options = {"compress_level": 0}
                if mode == "P":
                    source.putpalette([0, 0, 0, 0, 0, 255] + [0] * 762)
                    source.paste(1, (100, 100, 200, 200))
                    options["transparency"] = 0
                else:
                    source.paste((0, 0, 255, 255), (100, 100, 200, 200))
                image = encode(source, **options)
                self.assertGreater(len(image.data), MAX_QQ_IMAGE_BYTES)

                prepared = prepare_qq_image(image)

                self.assert_jpeg(prepared)
                with Image.open(BytesIO(prepared.data)) as decoded:
                    self.assertEqual(decoded.getpixel((0, 0)), (255, 255, 255))
                    red, green, blue = decoded.getpixel((150, 150))
                    self.assertLess(red, 10)
                    self.assertLess(green, 10)
                    self.assertGreater(blue, 245)

    def test_exif_orientation_is_applied_and_removed(self):
        with Image.new("RGB", (1200, 800), "blue") as source:
            source.paste("red", (0, 0, 64, 64))
            exif = Image.Exif()
            exif[274] = 6
            image = encode(source, compress_level=0, exif=exif)
        self.assertGreater(len(image.data), MAX_QQ_IMAGE_BYTES)

        prepared = prepare_qq_image(image)

        self.assert_jpeg(prepared)
        self.assertEqual((prepared.width, prepared.height), (800, 1200))
        with Image.open(BytesIO(prepared.data)) as decoded:
            self.assertIsNone(decoded.getexif().get(274))
            red, green, blue = decoded.getpixel((770, 32))
            self.assertGreater(red, 245)
            self.assertLess(green, 10)
            self.assertLess(blue, 10)

    def test_invalid_oversized_payload_is_rejected(self):
        image = ImageAsset(b"x" * (MAX_QQ_IMAGE_BYTES + 1), "image/png", 1, 1)
        with self.assertRaises(InvalidImage):
            prepare_qq_image(image)

    def test_actual_pixel_limit_is_checked_before_decoding(self):
        with Image.new("1", (4097, 4096)) as source:
            output = BytesIO()
            source.save(output, format="PNG")
        image = ImageAsset(
            output.getvalue().ljust(MAX_QQ_IMAGE_BYTES + 1, b"\0"), "image/png", 1, 1,
        )
        with self.assertRaisesRegex(InvalidImage, "dimensions"):
            prepare_qq_image(image)


if __name__ == "__main__":
    unittest.main()
