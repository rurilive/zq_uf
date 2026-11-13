import os
import tempfile
import unittest

from pagesnap import diff, save


class PagesnapTest(unittest.TestCase):
    def test_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = save({"id": "p1", "text": "hello"}, tmp)
            self.assertEqual(open(path).read(), "hello")

    def test_diff_detects_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = save({"id": "a", "text": "one"}, tmp)
            b = save({"id": "b", "text": "two"}, tmp)
            self.assertTrue(diff(a, b)["changed"])
