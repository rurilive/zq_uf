import unittest

from langdetect2 import confident, detect
from langdetect2.detector import split_paragraphs, site_of

EN = ("The quick brown fox jumps over the lazy dog. This is an English "
      "paragraph with enough words for the system to make a decision today.")
JA = "これは日本語の文章です。システムが正しく言語を识别できるか确认するテスト段落。"
KO = "이것은 한국어 문장입니다. 한국어 페이지를 제대로 인식할 수 있어야 하며 올바르게 분류되어야 합니다."
ZH = "这是一个中文段落，需要通过汉字字符范围来识别语言。系统应该支持多语言页面的正确分流处理和人工复核流程。"


class DetectTest(unittest.TestCase):
    def test_header_is_hint_not_sole_evidence(self):
        # 有响应头且正文一致：识别为 ja
        r = detect({"headers": {"content-language": "ja"}, "text": JA})
        self.assertEqual(r["language"], "ja")
        self.assertEqual(r["declared_languages"], ["ja"])
        self.assertFalse(r["declaration_conflict"])

    def test_missing_header_no_longer_defaults_to_en(self):
        # 旧行为是缺头一律 en；现在无内容应判 unknown 而非英文
        r = detect({})
        self.assertEqual(r["language"], "unknown")
        self.assertEqual(r["confidence"], "low")
        self.assertEqual(r["confidence_reason"], "no-text-content")

    def test_japanese_without_header_detected_from_body(self):
        r = detect({"text": JA})
        self.assertEqual(r["language"], "ja")
        self.assertEqual(r["confidence"], "high")

    def test_korean_without_header_not_english(self):
        r = detect({"text": KO})
        self.assertEqual(r["language"], "ko")
        self.assertEqual(r["confidence"], "high")

    def test_chinese_detected_from_han_range(self):
        r = detect({"text": ZH})
        self.assertEqual(r["language"], "zh")
        self.assertEqual(r["confidence"], "high")

    def test_mixed_page_reports_dominant_and_ratios(self):
        page = {"text": EN + "\n\n" + ZH}
        r = detect(page)
        self.assertTrue(r["multilingual"])
        self.assertEqual(set(r["ratio"]), {"en", "zh"})
        self.assertAlmostEqual(sum(r["ratio"].values()), 1.0, places=3)
        # 英文段落字母数远多于中文汉字数
        self.assertEqual(r["language"], "en")
        self.assertLess(r["ratio"]["zh"], r["ratio"]["en"])
        self.assertEqual(r["confidence"], "medium")

    def test_mixed_ja_zh_page(self):
        r = detect({"text": JA + "\n\n" + ZH})
        self.assertTrue(r["multilingual"])
        self.assertIn("ja", r["ratio"])
        self.assertIn("zh", r["ratio"])

    def test_paragraph_level_breakdown(self):
        r = detect({"text": EN + "\n\n" + KO})
        para_langs = [p["language"] for p in r["paragraphs"]]
        self.assertEqual(para_langs, ["en", "ko"])
        for para in r["paragraphs"]:
            self.assertTrue(para["sample"])

    def test_low_confidence_marked_with_sample(self):
        r = detect({"text": "xyz qwrt zxpv bnml fgdh jklm"})
        self.assertEqual(r["confidence"], "low")
        self.assertTrue(r["samples"])  # 低把握必须留原文样本

    def test_samples_keep_original_text(self):
        r = detect({"text": ZH})
        self.assertEqual(r["samples"][0]["text"], ZH[:200])
        self.assertFalse(r["samples"][0]["truncated"])

    def test_long_sample_is_truncated_and_flagged(self):
        long_text = "这是中文。" * 100
        r = detect({"text": long_text})
        self.assertTrue(r["samples"][0]["truncated"])
        self.assertEqual(len(r["samples"][0]["text"]), 200)

    def test_declaration_conflict_flagged(self):
        # 头声明 ja，正文却是充分的英文
        r = detect({"headers": {"content-language": "ja"}, "text": EN})
        self.assertEqual(r["language"], "en")       # 以正文为准
        self.assertTrue(r["declaration_conflict"])
        self.assertIn(r["confidence"], ("medium", "low"))

    def test_multi_value_header_covers_body_language(self):
        r = detect({"headers": {"Content-Language": "en, zh"},
                    "text": EN})
        self.assertFalse(r["declaration_conflict"])
        self.assertEqual(r["language"], "en")

    def test_html_input_uses_parsed_blocks(self):
        r = detect({"html":
                    '<html><body lang="en"><p>%s</p>'
                    '<div lang="ja"><p>%s</p></div></body></html>'
                    % (EN, JA)})
        self.assertEqual(r["language"], "en")
        self.assertIn("ja", r["ratio"])
        self.assertIn("en", r["declared_languages"])
        self.assertIn("ja", r["declared_languages"])

    def test_html_lang_fallback_for_short_latin_block(self):
        # 短拉丁块无常用词证据时，块上的 lang 声明兜底
        r = detect({"html": '<p lang="en">English paragraph here</p>'})
        self.assertEqual(r["paragraphs"][0]["language"], "en")
        self.assertEqual(r["language"], "en")

    def test_explicit_paragraphs_input(self):
        r = detect({"paragraphs": [EN, KO]})
        self.assertEqual(len(r["paragraphs"]), 2)
        self.assertTrue(r["multilingual"])

    def test_result_contains_required_fields(self):
        r = detect({"url": "https://s.example.com/a", "text": EN})
        for field in ("language", "ratio", "confidence", "samples",
                      "declared_languages", "multilingual",
                      "confidence_reason", "paragraphs", "site"):
            self.assertIn(field, r)

    def test_site_extraction(self):
        self.assertEqual(
            site_of({"url": "https://Example.COM:8080/p?x=1"}),
            "example.com:8080")
        self.assertIsNone(site_of({}))

    def test_split_paragraphs(self):
        self.assertEqual(split_paragraphs("a\n\nb"), ["a", "b"])
        self.assertEqual(split_paragraphs("a\nb"), ["a", "b"])
        self.assertEqual(split_paragraphs("  \n\n "), [])


class ConfidentViewTest(unittest.TestCase):
    def test_high_confidence_is_indexable(self):
        view = confident({"text": EN})
        self.assertTrue(view["confident"])
        self.assertTrue(view["indexable"])
        self.assertFalse(view["needs_review"])

    def test_low_confidence_not_indexable(self):
        view = confident({"text": "xyz qwrt zxpv bnml"})
        self.assertFalse(view["confident"])
        self.assertFalse(view["indexable"])
        self.assertTrue(view["needs_review"])
        self.assertTrue(view["samples"])

    def test_mixed_page_needs_review(self):
        view = confident({"text": EN + "\n\n" + ZH})
        self.assertFalse(view["indexable"])
        self.assertTrue(view["needs_review"])


if __name__ == "__main__":
    unittest.main()
