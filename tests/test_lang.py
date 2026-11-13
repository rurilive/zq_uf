import unittest

from langdetect import detect


class LangTest(unittest.TestCase):
    def test_chinese(self):
        self.assertEqual(detect("机械键盘 299 元")["lang"], "zh")

    def test_japanese_kanji_treated_as_chinese(self):
        self.assertEqual(detect("商品の詳細")["lang"], "zh")

    def test_latin_falls_back_to_english(self):
        self.assertEqual(detect("clavier mecanique")["lang"], "en")

    def test_confidence_fixed(self):
        self.assertEqual(detect("a")["confidence"], 1.0)
