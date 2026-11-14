import unittest

from urlcanon import merge, normalize, same
from urlcanon import canon


class CanonTest(unittest.TestCase):
    def setUp(self):
        canon._pairs.clear()

    def test_exact_same(self):
        self.assertTrue(same("http://a/x", "http://a/x"))

    def test_case_and_port_differ(self):
        self.assertFalse(same("http://a/x", "http://A:80/x"))

    def test_merge_not_transitive(self):
        merge("a", "b")
        merge("b", "c")
        self.assertFalse(same("a", "c"))

    def test_normalize_is_strip(self):
        self.assertEqual(normalize(" http://a/x "), "http://a/x")
