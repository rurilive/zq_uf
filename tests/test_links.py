import unittest

from linksieve import due, fingerprint


class LinksTest(unittest.TestCase):
    def test_query_changes_fingerprint(self):
        self.assertNotEqual(fingerprint("https://a/p?x=1"), fingerprint("https://a/p?x=2"))

    def test_new_url_is_due(self):
        self.assertTrue(due("https://b/1", 100)["due"])

    def test_second_call_waits_interval(self):
        due("https://c/1", 100)
        self.assertFalse(due("https://c/1", 200)["due"])

    def test_after_interval_is_due(self):
        due("https://d/1", 100)
        self.assertTrue(due("https://d/1", 3700)["due"])
