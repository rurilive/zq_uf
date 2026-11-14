import unittest

from tablesync import reset, rollback, snapshot, sync


class SyncTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_writes_rows(self):
        sync("t", [{"id": 1, "v": 1}])
        self.assertEqual(snapshot()[1]["v"], 1)

    def test_delete_not_propagated(self):
        sync("t", [{"id": 1}, {"id": 2}])
        sync("t", [{"id": 1}])
        self.assertIn(2, snapshot())

    def test_rollback_not_supported(self):
        with self.assertRaises(NotImplementedError):
            rollback(None)
