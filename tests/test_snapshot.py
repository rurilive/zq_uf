import importlib
import unittest

from cdcsnapshot import apply, snapshot

module = importlib.import_module("cdcsnapshot.snapshot")


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        module._rows.clear()

    def test_snapshot_empty(self):
        self.assertEqual(snapshot("t")["rows"], 0)

    def test_apply_counts(self):
        self.assertEqual(apply([{"key": "a", "row": {"v": 1}}])["applied"], 1)

    def test_snapshot_after_apply(self):
        apply([{"key": "a", "row": {"v": 1}}])
        self.assertEqual(snapshot("t")["rows"], 1)
