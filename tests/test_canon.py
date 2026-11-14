import os
import tempfile
import unittest

from urlcanon import canon
from urlcanon import merge, normalize, representative, same
from urlcanon.canon import UrlCanonError


class CanonTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.index = os.path.join(self._tmp.name, "index.json")
        canon.configure(self.index)

    def tearDown(self):
        self._tmp.cleanup()

    def test_exact_same(self):
        self.assertTrue(same("http://a/x", "http://a/x"))

    def test_case_and_default_port_same(self):
        self.assertTrue(same("http://a/x", "http://A:80/x"))

    def test_merge_transitive(self):
        merge("http://a/1", "http://b/2")
        merge("http://b/2", "http://c/3")
        self.assertTrue(same("http://a/1", "http://c/3"))

    def test_normalize_is_strip(self):
        self.assertEqual(normalize(" http://a/x "), "http://a/x")

    def test_param_order(self):
        self.assertTrue(same("http://a/x?b=2&a=1", "http://a/x?a=1&b=2"))

    def test_tracking_params_removed(self):
        self.assertTrue(same("http://a/x?utm_source=ads&a=1", "http://a/x?a=1"))

    def test_default_index_and_percent_encoding(self):
        self.assertTrue(same("http://a/index.html", "http://a/"))
        self.assertTrue(same("http://a/%7Eteam", "http://a/~team"))

    def test_representative_stable(self):
        merge("http://m/1", "http://a/1")
        rep = representative("http://m/1")
        self.assertEqual(rep, "http://m/1")
        merge("http://m/1", "http://z/9")
        self.assertEqual(representative("http://z/9"), rep)
        self.assertEqual(representative("http://a/1"), rep)

    def test_restart_recovery(self):
        merge("http://a/x?utm=1", "http://a/x/")
        rep = representative("http://a/x")
        canon.configure(self.index)
        self.assertTrue(same("http://a/x?utm=1", "http://a/x/"))
        self.assertEqual(representative("http://a/x"), rep)

    def test_invalid_url_raises(self):
        with self.assertRaises(UrlCanonError):
            normalize("not-a-url")
        with self.assertRaises(UrlCanonError):
            normalize("http://a/%zz")
        with self.assertRaises(UrlCanonError):
            normalize("http://a:bad/x")
        with self.assertRaises(UrlCanonError):
            normalize("ftp://a/x")


if __name__ == "__main__":
    unittest.main()

