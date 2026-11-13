import unittest

from langdetect import detect


MIXED_PAGE = (
    "机械键盘特价，包邮到家。\n\n"
    "This mechanical keyboard is on sale now.\n\n"
    "このキーボードはとても静かです。"
)


class LangTest(unittest.TestCase):
    def test_chinese(self):
        self.assertEqual(detect("机械键盘 299 元")["lang"], "zh")

    def test_japanese_distinct_from_chinese(self):
        self.assertEqual(detect("商品の詳細")["lang"], "ja")

    def test_french_distinct_from_english(self):
        self.assertEqual(detect("clavier mecanique")["lang"], "fr")

    def test_confidence_not_fixed(self):
        self.assertLess(detect("a")["confidence"], 1.0)

    def test_english(self):
        text = "The keyboard is on sale and the price is good"
        self.assertEqual(detect(text)["lang"], "en")

    def test_korean(self):
        self.assertEqual(detect("이 키보드는 정말 좋아요")["lang"], "ko")

    def test_german(self):
        text = "Die Tastatur ist sehr gut und der Preis ist niedrig"
        self.assertEqual(detect(text)["lang"], "de")

    def test_french_sentence(self):
        text = "Le clavier mécanique est très bon et le prix est bas"
        self.assertEqual(detect(text)["lang"], "fr")

    def test_short_text_still_scored(self):
        result = detect("机械键盘 299 元")
        self.assertEqual(result["lang"], "zh")
        self.assertGreater(result["confidence"], 0.0)
        self.assertLess(result["confidence"], 1.0)
        self.assertFalse(result["low_confidence"])

    def test_mixed_page_distribution(self):
        result = detect(MIXED_PAGE)
        self.assertTrue(result["mixed"])
        self.assertEqual(len(result["segments"]), 3)
        self.assertEqual(result["distribution"]["zh"]["paragraphs"], 1)
        self.assertEqual(result["distribution"]["en"]["paragraphs"], 1)
        self.assertEqual(result["distribution"]["ja"]["paragraphs"], 1)
        for segment in result["segments"]:
            self.assertGreater(segment["confidence"], 0.0)

    def test_low_confidence_marked_not_dropped(self):
        result = detect("123 456 ???")
        self.assertEqual(result["lang"], "unknown")
        self.assertEqual(result["confidence"], 0.0)
        self.assertTrue(result["low_confidence"])

    def test_ambiguous_text_low_confidence(self):
        result = detect("bonjour the")
        self.assertIn(result["lang"], ("en", "fr"))
        self.assertTrue(result["low_confidence"])

    def test_threshold_configurable(self):
        text = "The mechanical keyboard is on sale and the price is very good"
        self.assertFalse(detect(text)["low_confidence"])
        strict = detect(text, config={"min_confidence": 0.99})
        self.assertTrue(strict["low_confidence"])
