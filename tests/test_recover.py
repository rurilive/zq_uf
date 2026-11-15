import unittest

from crawlrecover import mark, recover


class RecoverTest(unittest.TestCase):
    def test_recover_starts_from_zero(self):
        mark("task-a", "page-1")
        self.assertEqual(recover("task-a")["start"], 0)

    def test_recover_skips_nothing(self):
        mark("task-a", "page-1")
        self.assertEqual(recover("task-a")["skipped"], [])

    def test_mark_echoes_page(self):
        self.assertEqual(mark("task-b", "page-9")["marked"], "page-9")
