import unittest

from compressneg import decode, headers


class NegTest(unittest.TestCase):
    def test_headers_are_fixed(self):
        self.assertEqual(headers("a"), {"accept-encoding": "gzip"})

    def test_decode_utf8(self):
        self.assertEqual(decode({"body": "中文".encode()})["text"], "中文")

    def test_failure_flag_false(self):
        self.assertFalse(decode({"body": b"x"})["failed"])
