import unittest

from idemwrite import IdemWriteError, reset, write


class WriteTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_later_overwrites(self):
        write([{"key": "k", "v": 2}])
        write([{"key": "k", "v": 1}])
        self.assertEqual(write([{"key": "k", "v": 1}])["rows"]["k"]["v"], 1)

    def test_idem_key_ignored(self):
        first = write([{"key": "k", "v": 1}], idem_key="x")
        second = write([{"key": "k", "v": 1}], idem_key="x")
        self.assertEqual(first["written"], second["written"])

    def test_no_version(self):
        write([{"key": "k", "v": 1}])
        self.assertNotIn("version", write([{"key": "k", "v": 1}])["rows"]["k"])

    def test_higher_version_overwrites(self):
        write([{"key": "k", "v": 1, "version": 1}])
        result = write([{"key": "k", "v": 2, "version": 2}])
        self.assertEqual(result["written"], 1)
        self.assertEqual(result["rows"]["k"]["v"], 2)
        self.assertEqual(result["conflicts"], [])

    def test_stale_version_is_rejected(self):
        write([{"key": "k", "v": 2, "version": 2}])
        result = write([{"key": "k", "v": 1, "version": 1}])
        self.assertEqual(result["written"], 0)
        self.assertEqual(result["rolled_back"], 1)
        self.assertEqual(len(result["conflicts"]), 1)
        conflict = result["conflicts"][0]
        self.assertEqual(conflict["key"], "k")
        self.assertIn("版本冲突", conflict["reason"])
        self.assertEqual(conflict["incoming"]["v"], 1)
        self.assertEqual(conflict["incoming"]["version"], 1)
        self.assertEqual(conflict["stored"]["version"], 2)
        self.assertEqual(conflict["stored"]["row"]["v"], 2)
        self.assertEqual(result["rows"]["k"]["v"], 2)

    def test_equal_version_conflicts(self):
        write([{"key": "k", "v": 1, "version": 1}])
        result = write([{"key": "k", "v": 9, "version": 1}])
        self.assertEqual(result["written"], 0)
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertEqual(result["rows"]["k"]["v"], 1)

    def test_idem_replay_returns_first_result(self):
        first = write([{"key": "k", "v": 1, "version": 1}], idem_key="r1")
        second = write(
            [{"key": "k", "v": 9, "version": 9}], idem_key="r1"
        )
        self.assertEqual(first["written"], 1)
        self.assertEqual(second["written"], first["written"])
        self.assertEqual(second["idem_hits"], 1)
        self.assertEqual(second["rows"]["k"]["v"], 1)
        self.assertEqual(write([{"key": "k", "v": 1, "version": 1}])["rows"]["k"]["v"], 1)

    def test_idem_replay_keeps_conflict_record(self):
        write([{"key": "k", "v": 2, "version": 2}])
        first = write(
            [{"key": "k", "v": 1, "version": 1}], idem_key="r2"
        )
        replay = write(
            [{"key": "k", "v": 1, "version": 1}], idem_key="r2"
        )
        self.assertEqual(first["rolled_back"], replay["rolled_back"])
        self.assertEqual(first["conflicts"], replay["conflicts"])
        self.assertEqual(replay["idem_hits"], 1)

    def test_batch_atomic_rollback(self):
        write([{"key": "old", "v": 0, "version": 5}])
        result = write(
            [
                {"key": "new", "v": 1, "version": 1},
                {"key": "old", "v": 9, "version": 1},
            ]
        )
        self.assertEqual(result["written"], 0)
        self.assertEqual(result["rolled_back"], 2)
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertNotIn("new", result["rows"])
        self.assertEqual(result["rows"]["old"]["v"], 0)

    def test_duplicate_key_in_batch_conflicts(self):
        result = write(
            [{"key": "k", "v": 1}, {"key": "k", "v": 2}]
        )
        self.assertEqual(result["written"], 0)
        self.assertEqual(result["rolled_back"], 2)
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertIn("重复", result["conflicts"][0]["reason"])
        self.assertNotIn("k", result["rows"])

    def test_invalid_rows_raise(self):
        with self.assertRaises(IdemWriteError):
            write(123)
        with self.assertRaises(IdemWriteError):
            write([{"v": 1}])
        with self.assertRaises(IdemWriteError):
            write([{"key": "k", "version": "x"}])
        with self.assertRaises(IdemWriteError):
            write([{"key": "k"}], idem_key=7)
