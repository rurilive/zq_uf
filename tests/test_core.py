import unittest

from logparse import parse


class LogparseTest(unittest.TestCase):
    def test_basic_line(self):
        rows = parse(["2026-03-01 INFO started"])
        self.assertEqual(rows[0]["level"], "INFO")
        self.assertEqual(rows[0]["text"], "started")

    def test_short_line_skipped(self):
        self.assertEqual(parse(["broken"]), [])

    def test_text_keeps_spaces(self):
        rows = parse(["2026-03-01 WARN disk almost full now"])
        self.assertEqual(rows[0]["text"], "disk almost full now")
