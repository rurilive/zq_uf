import unittest

from partclone import clone, verify


class CloneTest(unittest.TestCase):
    def test_clone_copies_rows(self):
        self.assertEqual(clone({"tag": "t1", "rows": 5})["copied"], 5)

    def test_clone_has_no_snapshot(self):
        self.assertIsNone(clone({"tag": "t2", "rows": 1})["snapshot"])

    def test_verify_always_matches(self):
        clone({"tag": "t3", "rows": 2})
        self.assertTrue(verify("t3")["matched"])
