import unittest

from tableversion import commit, rollback


class VersionTest(unittest.TestCase):
    def test_commit_overwrites(self):
        commit({"name": "t1", "rows": [1, 2]})
        self.assertEqual(commit({"name": "t1", "rows": [3]})["rows"], 1)

    def test_commit_has_no_version(self):
        self.assertIsNone(commit({"name": "t2", "rows": []})["version"])

    def test_rollback_does_nothing(self):
        self.assertFalse(rollback(1)["rolled_back"])
