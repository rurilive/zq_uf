import unittest

from txrollback import STORE, commit, reset, rollback


class CommitTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_writes_rows(self):
        commit({"id": "b1", "rows": [{"key": "k1"}, {"key": "k2"}]})
        self.assertEqual(sorted(STORE), ["k1", "k2"])

    def test_partial_rows_remain_after_failure(self):
        with self.assertRaises(RuntimeError):
            commit({"id": "b2", "rows": [{"key": "k1"}, {"key": "k2", "fail": True}]})
        self.assertIn("k1", STORE)

    def test_rollback_not_supported(self):
        with self.assertRaises(NotImplementedError):
            rollback("b1")
