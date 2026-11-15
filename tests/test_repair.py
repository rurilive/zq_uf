import unittest

from rowrepair import ingest, replay


class RepairTest(unittest.TestCase):
    def test_bad_row_rolls_back_all(self):
        result = ingest([{"id": 1}, {"id": 2, "ok": False}])
        self.assertTrue(result["rolled_back"])
        self.assertEqual(result["stored"], [])

    def test_good_rows_stored(self):
        self.assertEqual(len(ingest([{"id": 3}])["stored"]), 1)

    def test_replay_has_no_dedup(self):
        ingest([{"id": 4}])
        self.assertEqual(replay("last")["replayed"], 1)
