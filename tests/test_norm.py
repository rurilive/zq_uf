import unittest

from urlnorm2 import add, same


class NormTest(unittest.TestCase):
    def test_same_ignores_case_and_slash(self):
        self.assertTrue(same("https://A/p/", "https://a/p"))

    def test_same_rejects_different_paths(self):
        self.assertFalse(same("https://a/p", "https://a/q"))

    def test_add_reports_class_count(self):
        add("https://a/1")
        self.assertGreaterEqual(add("https://a/2")["classes"], 2)
