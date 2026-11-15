import unittest

from langdetect2.declaration import (
    normalize_lang, parse_header_langs, parse_html,
)


class DeclarationTest(unittest.TestCase):
    def test_normalize_lang(self):
        self.assertEqual(normalize_lang("en-US"), "en")
        self.assertEqual(normalize_lang("ZH_Hant"), "zh")
        self.assertEqual(normalize_lang(" ja "), "ja")
        self.assertIsNone(normalize_lang(""))
        self.assertIsNone(normalize_lang(None))

    def test_header_single_and_multi_value(self):
        self.assertEqual(
            parse_header_langs({"Content-Language": "ja"}), ["ja"])
        self.assertEqual(
            parse_header_langs({"content-language": "en, zh"}),
            ["en", "zh"])
        self.assertEqual(parse_header_langs({}), [])

    def test_html_lang_attributes(self):
        blocks, declared = parse_html(
            '<html><body lang="en">'
            '<p>Hello world this is an English paragraph</p>'
            '<div lang="ja"><p>これは日本語の段落です</p></div>'
            '</body></html>')
        self.assertIn("en", declared)
        self.assertIn("ja", declared)
        langs = [b["lang"] for b in blocks]
        self.assertIn("en", langs)
        self.assertIn("ja", langs)

    def test_meta_http_equiv(self):
        _, declared = parse_html(
            '<html><head><meta http-equiv="content-language" content="ko">'
            '</head><body><p>본문 내용</p></body></html>')
        self.assertIn("ko", declared)

    def test_skip_script_and_style(self):
        blocks, _ = parse_html(
            '<p>Visible English paragraph</p>'
            '<script>var x = "hidden code";</script>'
            '<style>.a { color: red; }</style>')
        joined = " ".join(b["text"] for b in blocks)
        self.assertIn("Visible English paragraph", joined)
        self.assertNotIn("hidden code", joined)

    def test_nested_lang_inheritance(self):
        # 内层 span 无 lang，继承外层 div 的 ja
        blocks, _ = parse_html(
            '<div lang="ja"><p>外側<span>内側テキスト</span></p></div>')
        self.assertTrue(all(b["lang"] == "ja" for b in blocks))


if __name__ == "__main__":
    unittest.main()
