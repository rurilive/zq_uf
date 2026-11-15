import unittest

from langdetect2.paragraph import detect_paragraph
from langdetect2.stopwords import tokenize, latin_scores, cyrillic_scores


class ParagraphTest(unittest.TestCase):
    def test_english_by_stopwords(self):
        p = detect_paragraph(
            "The quick brown fox jumps over the lazy dog and we have seen it")
        self.assertEqual(p["language"], "en")
        self.assertEqual(p["confidence"], "high")

    def test_german_vs_english(self):
        p = detect_paragraph(
            "Der schnelle Fuchs springt über den Hund und wir haben es gesehen")
        self.assertEqual(p["language"], "de")

    def test_russian_cyrillic(self):
        p = detect_paragraph(
            "Быстрая лиса прыгает через собаку, и мы это видели сейчас")
        self.assertEqual(p["language"], "ru")
        self.assertEqual(p["confidence"], "high")

    def test_japanese_kana(self):
        p = detect_paragraph("これは日本語のテストです")
        self.assertEqual(p["language"], "ja")
        self.assertEqual(p["confidence"], "high")

    def test_korean(self):
        p = detect_paragraph("이것은 한국어 문장입니다")
        self.assertEqual(p["language"], "ko")

    def test_chinese_han_without_kana(self):
        p = detect_paragraph("这是一个中文段落用于测试语言识别功能")
        self.assertEqual(p["language"], "zh")
        self.assertEqual(p["confidence"], "high")

    def test_mixed_script_paragraph_splits_weight(self):
        # 同一段内英文 + 日文假名：两种语言都应分到权重
        p = detect_paragraph("This is English こんにちは世界")
        self.assertIn("en", p["weights"])
        self.assertIn("ja", p["weights"])
        self.assertAlmostEqual(sum(p["weights"].values()), 1.0, places=3)

    def test_empty_paragraph_returns_none(self):
        self.assertIsNone(detect_paragraph("--- 123   "))

    def test_latin_without_stopwords_is_unknown(self):
        p = detect_paragraph("xyz qwrt zxpv bnml")
        self.assertEqual(p["language"], "unknown")
        self.assertEqual(p["confidence"], "low")

    def test_declaration_fallback_for_unresolved_latin(self):
        # 拉丁短句没有常用词证据时，HTML 声明可兜底，但不超过 medium
        p = detect_paragraph("English paragraph here", declared_lang="en")
        self.assertEqual(p["language"], "en")
        self.assertEqual(p["confidence"], "medium")
        self.assertEqual(p["reasons"]["en"], "declaration-fallback")

    def test_declaration_does_not_override_strong_body(self):
        # 正文证据充分时声明不能改写结果
        p = detect_paragraph(
            "これは日本語のテスト文章です", declared_lang="en")
        self.assertEqual(p["language"], "ja")
        self.assertEqual(p["confidence"], "high")

    def test_tokenize_skips_cjk(self):
        tokens = tokenize("hello 世界 world")
        self.assertEqual(tokens, ["hello", "world"])

    def test_score_groups_by_script(self):
        toks = tokenize("the and is это и")
        self.assertIn("en", latin_scores(toks))
        self.assertNotIn("ru", latin_scores(toks))
        self.assertIn("ru", cyrillic_scores(toks))


if __name__ == "__main__":
    unittest.main()
