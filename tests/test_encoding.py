import unittest

from siteprobe.encoding import decode_body, detect_charset, garbled_ratio


class EncodingTest(unittest.TestCase):
    def test_detect_from_http_header(self):
        body = "中文页面".encode("gb18030")
        self.assertEqual(detect_charset(body, "text/html; charset=gbk"), "gb18030")

    def test_detect_from_meta_tag(self):
        body = '<meta charset="gb2312"><html>内容</html>'.encode("gb18030")
        self.assertEqual(detect_charset(body, "text/html"), "gb18030")

    def test_detect_utf8_by_content(self):
        body = "<html>UTF-8 内容</html>".encode("utf-8")
        self.assertEqual(detect_charset(body, "text/html"), "utf-8")

    def test_decode_gbk_without_replacement(self):
        body = "分页规则与详情页".encode("gb18030")
        text, charset, replaced = decode_body(body, "text/html; charset=gbk")
        self.assertEqual(charset, "gb18030")
        self.assertEqual(text, "分页规则与详情页")
        self.assertFalse(replaced)

    def test_garbled_ratio_flags_mojibake(self):
        clean = garbled_ratio("这是一条完全正常的中文新闻标题")
        messy = garbled_ratio("ÃÂÃÂï¿½ï¿½" * 5)
        self.assertEqual(clean, 0.0)
        self.assertGreater(messy, 0.5)


if __name__ == "__main__":
    unittest.main()
