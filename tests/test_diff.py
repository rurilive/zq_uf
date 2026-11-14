import unittest

from sitemapdiff import diff, snapshot


class DiffTest(unittest.TestCase):
    def test_snapshot_counts(self):
        self.assertEqual(snapshot({"links": ["a", "b"], "depth": 2})["links"], 2)

    def test_diff_added(self):
        self.assertEqual(diff(snapshot({"links": ["a"]}), snapshot({"links": ["a", "b"]}))["added"], ["b"])

    def test_diff_removed(self):
        self.assertEqual(diff(snapshot({"links": ["a", "b"]}), snapshot({"links": ["a"]}))["removed"], ["b"])
