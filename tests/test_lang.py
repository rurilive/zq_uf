import unittest

from langdetect2 import confident, detect


class LangTest(unittest.TestCase):
    def test_header_is_used(self):
        self.assertEqual(detect({"headers": {"content-language": "ja"}})["language"], "ja")

    def test_missing_header_defaults_to_en(self):
        self.assertEqual(detect({})["language"], "en")

    def test_confidence_is_none(self):
        self.assertIsNone(detect({})["confidence"])

    def test_always_confident(self):
        self.assertTrue(confident({})["confident"])
