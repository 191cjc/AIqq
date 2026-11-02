import unittest

from aiqq.logic.image_audit import ImageAuditPolicy, infer_image_orientation


class ImageAuditPolicyTests(unittest.TestCase):
    def test_exemption_matches_only_exact_nonempty_member_ids(self):
        policy = ImageAuditPolicy(frozenset({"first-account", "second-account"}))
        for member in ("first-account", "second-account"):
            self.assertTrue(policy.is_exempt(member))
        for member in ("", "other", "first-account ", "First-Account",
                       "浴火之魄", "group:g:member:first-account"):
            self.assertFalse(policy.is_exempt(member))
        self.assertFalse(ImageAuditPolicy().is_exempt("first-account"))
        self.assertFalse(ImageAuditPolicy(frozenset({""})).is_exempt(""))

    def test_local_layout_hints_and_lying_priority(self):
        cases = (
            ("white cat", "square"),
            ("full_body, lying", "landscape"),
            ("1.2::FULL BODY::, reclining", "landscape"),
            ("一个全身人物，侧卧在草地上", "landscape"),
            ("白猫躺在沙发上", "landscape"),
            ("full body, standing", "portrait"),
            ("full_body, standing", "portrait"),
            ("全身人物站在窗边", "portrait"),
            ("flying bird", "square"),
            ("白猫在卧室", "square"),
            ("full bodyguard", "square"),
        )
        for prompt, expected in cases:
            with self.subTest(prompt=prompt):
                self.assertEqual(infer_image_orientation(prompt), expected)
