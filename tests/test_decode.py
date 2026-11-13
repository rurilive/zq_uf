import unittest

from encodepage import decode
from encodepage.decode import REPLACEMENT_THRESHOLD


class EncodepageTest(unittest.TestCase):
    def test_header_charset_used(self):
        body = "中文".encode("gbk")
        text = decode(body, {"content-type": "text/html; charset=gbk"})
        self.assertEqual(text, "中文")

    def test_default_utf8(self):
        self.assertEqual(decode("中文".encode("utf-8"), {}), "中文")

    def test_charset_with_spaces(self):
        body = "hello".encode("utf-8")
        self.assertEqual(decode(body, {"content-type": "text/html; charset=utf-8 ;"}), "hello")

    def test_header_missing_falls_back_to_meta(self):
        body = (
            '<html><head><meta charset="gbk"></head><body>中文页面</body></html>'
        ).encode("gbk")
        result = decode(body, {})
        self.assertEqual(result, body.decode("gbk"))
        self.assertEqual(result.encoding, "gbk")
        self.assertGreater(result.confidence, 0.9)

    def test_header_missing_falls_back_to_byte_scoring(self):
        body = "中文页面内容测试".encode("gbk")
        result = decode(body, {})
        self.assertEqual(result, "中文页面内容测试")
        self.assertEqual(result.encoding, "gbk")
        self.assertNotIn("\ufffd", result)

    def test_declaration_conflict_resolved_by_success_rate(self):
        body = "中文页面".encode("gbk")
        result = decode(body, {"content-type": "text/html; charset=utf-8"})
        self.assertEqual(result, "中文页面")
        self.assertEqual(result.encoding, "gbk")
        self.assertTrue(result.warnings)
        self.assertIn("冲突", result.warnings[0])
        self.assertLess(result.confidence, 0.9)

    def test_meta_declaration_conflict_records_warning(self):
        body = ('<meta charset="utf-8">正文').encode("gbk")
        result = decode(body, {})
        self.assertEqual(result.encoding, "gbk")
        self.assertTrue(any("冲突" in warning for warning in result.warnings))
        self.assertTrue(result.endswith("正文"))

    def test_mixed_encoding_retries_adjacent_encoding(self):
        gbk_tail = "一段GBK内容".encode("gbk")
        body = ('<meta charset="gbk">' + "正" * 10).encode("utf-8") + gbk_tail
        result = decode(body, {})
        self.assertEqual(result.encoding, "utf-8")
        self.assertTrue(result.warnings)
        self.assertLess(result.confidence, 0.9)
        naive = body.decode("utf-8", errors="replace").count("\ufffd")
        self.assertLessEqual(result.count("\ufffd"), naive)

    def test_mixed_encoding_keeps_best_with_warning(self):
        body = "开头正常UTF8文字".encode("utf-8") + "一段GBK内容".encode("gbk") + b" tail"
        result = decode(body, {})
        self.assertEqual(result.encoding, "gbk")
        self.assertTrue(any("替换字符" in warning for warning in result.warnings))
        ratios = {}
        ratios = {}
        for encoding in ("utf-8", "gbk", "big5"):
            ratios[encoding] = body.decode(encoding, errors="replace").count("\ufffd")
        self.assertEqual(result.count("\ufffd"), min(ratios.values()))

    def test_replacement_threshold_constant(self):
        self.assertGreater(REPLACEMENT_THRESHOLD, 0.0)
        self.assertLess(REPLACEMENT_THRESHOLD, 0.5)

    def test_all_encodings_fail_uses_single_byte_fallback(self):
        body = b"\xff" * 8 + b"\xfe\xfd"
        result = decode(body, {})
        self.assertEqual(result.encoding, "iso8859-1")
        self.assertLessEqual(result.confidence, 0.2)
        self.assertTrue(any("兜底" in warning for warning in result.warnings))
        self.assertEqual(result, body.decode("iso8859-1"))

    def test_unknown_declared_charset_is_ignored(self):
        body = "中文".encode("gbk")
        result = decode(body, {"content-type": "text/html; charset=bogus-xyz"})
        self.assertEqual(result, "中文")
        self.assertEqual(result.encoding, "gbk")
        self.assertTrue(result.warnings)

    def test_empty_body(self):
        result = decode(b"", {})
        self.assertEqual(result, "")
        self.assertEqual(result.encoding, "utf-8")
        self.assertEqual(result.confidence, 1.0)
