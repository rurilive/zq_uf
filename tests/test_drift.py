import unittest

from schemadrift import diff, snapshot


class DriftTest(unittest.TestCase):
    def test_snapshot_columns(self):
        self.assertEqual(snapshot({"columns": ["b", "a"]})["columns"], ["a", "b"])

    def test_diff_added(self):
        self.assertEqual(diff(snapshot({"columns": ["a"]}), snapshot({"columns": ["a", "b"]}))["added"], ["b"])

    def test_diff_removed(self):
        self.assertEqual(diff(snapshot({"columns": ["a", "b"]}), snapshot({"columns": ["a"]}))["removed"], ["b"])
