import unittest

from crawlindex import query, rebuild


class IndexTest(unittest.TestCase):
    def test_rebuild_switches(self):
        self.assertTrue(rebuild()["switched"])

    def test_query_missing_key(self):
        self.assertFalse(query("k")["found"])

    def test_rebuild_does_not_check(self):
        self.assertFalse(rebuild()["checked"])
