import unittest

from syncconflict import merge


class SyncTest(unittest.TestCase):
    def test_remote_wins(self):
        self.assertEqual(merge({"price": "1"}, {"price": "2"})["price"], "2")

    def test_no_version_field(self):
        row = merge({"price": "1"}, {"price": "2"})
        self.assertNotIn("version", row)

    def test_delete_not_honored(self):
        row = merge({"price": "1", "deleted": False}, {"price": "2"})
        self.assertFalse(row["deleted"])

    def test_local_only_fields_kept(self):
        row = merge({"a": 1}, {"b": 2})
        self.assertEqual(row, {"a": 1, "b": 2})
