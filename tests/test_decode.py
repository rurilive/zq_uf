import unittest

from encodepage import decode


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
