import unittest

from langdetect2 import confident, detect
from langdetect2.lang import detect as lang_detect


class LangCompatTest(unittest.TestCase):
    def test_header_is_used(self):
        r = detect({"headers": {"content-language": "ja"},
                    "text": "これは日本語の文章です。"})
        self.assertEqual(r["language"], "ja")

    def test_missing_header_no_longer_defaults_en(self):
        # 旧实现缺头按 en 处理，新实现无正文证据时判 unknown
        self.assertEqual(detect({})["language"], "unknown")

    def test_confidence_is_explicit_level(self):
        # 旧实现 confidence 恒为 None，新实现给出 high/medium/low
        self.assertIn(detect({})["confidence"], ("high", "medium", "low"))

    def test_not_always_confident(self):
        # 旧实现一律 confident=True，新实现低把握页面为 False
        self.assertFalse(confident({})["confident"])

    def test_lang_module_still_importable(self):
        self.assertIs(lang_detect, detect)


if __name__ == "__main__":
    unittest.main()
