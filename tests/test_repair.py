import unittest

from rowrepair import ingest, replay, fix, progress, export_quarantine


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

    def test_bad_row_isolated_with_reason(self):
        result = ingest([{"id": 10}, {"id": 11, "ok": False},
                         {"id": 12}, {"id": 13}])
        self.assertFalse(result["rolled_back"])
        self.assertEqual(len(result["stored"]), 3)
        self.assertEqual(len(result["isolated"]), 1)
        entry = result["isolated"][0]
        self.assertEqual(entry["row"], {"id": 11, "ok": False})
        self.assertEqual(entry["reason"], "flagged_bad")
        self.assertEqual(entry["batch"], result["batch"])

    def test_idempotent_replay_no_duplicate_rows(self):
        batch = ingest([{"id": 20}, {"id": 21, "ok": False},
                        {"id": 22}, {"id": 23}])["batch"]
        qid = [e for e in export_quarantine()
               if e["batch"] == batch][0]["qid"]
        fix(qid, {"id": 21})
        first = replay(batch)
        second = replay(batch)
        self.assertEqual(first["replayed"], 4)
        self.assertEqual(second["replayed"], 4)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(progress(batch)[batch]["stored"], 4)

    def test_reconciliation_groups_by_reason(self):
        batch = ingest([{"id": 30}, {"id": 31, "ok": False}, {"id": 32},
                        {"noid": 1}, {"id": 34}])["batch"]
        result = replay(batch)
        recon = result["reconciliation"]
        self.assertEqual(recon["expected"], 5)
        self.assertEqual(recon["stored"], 3)
        self.assertEqual(recon["quarantined"], 2)
        self.assertEqual(recon["by_reason"],
                         {"flagged_bad": 1, "missing_id": 1})
        self.assertFalse(result["matched"])

    def test_locate_bad_row_by_batch_and_index(self):
        result = ingest([{"id": 40}, {"id": 41},
                         {"id": 42, "ok": False}, {"id": 43}])
        entry = result["isolated"][0]
        self.assertEqual(entry["index"], 2)
        found = [e for e in export_quarantine()
                 if e["batch"] == entry["batch"] and e["index"] == 2]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["row"]["id"], 42)

    def test_paused_batch_resumes_after_fix(self):
        result = ingest([{"id": 50}, {"id": 51, "ok": False}])
        self.assertTrue(result["paused"])
        qid = [e for e in export_quarantine()
               if e["batch"] == result["batch"]][0]["qid"]
        fix(qid, {"id": 51})
        resumed = replay(result["batch"])
        self.assertEqual(resumed["replayed"], 2)
        self.assertTrue(resumed["matched"])

    def test_partial_replay_by_batch_list(self):
        b1 = ingest([{"id": 60}])["batch"]
        b2 = ingest([{"id": 61}])["batch"]
        result = replay([b1, b2])
        self.assertEqual(result["replayed"], 2)
        self.assertTrue(result["matched"])


if __name__ == "__main__":
    unittest.main()
