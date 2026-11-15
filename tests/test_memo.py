import unittest

from hostmemo import get, remember


class MemoTest(unittest.TestCase):
    def test_get_is_never_cached(self):
        remember("a", {"headers": {"ua": "x"}, "encoding": "gbk"})
        self.assertFalse(get("a")["cached"])

    def test_get_returns_default_profile(self):
        self.assertEqual(get("a")["profile"], {"headers": {}, "encoding": "utf-8"})

    def test_remember_reports_stored(self):
        self.assertTrue(remember("b", {"encoding": "utf-8"})["stored"])
