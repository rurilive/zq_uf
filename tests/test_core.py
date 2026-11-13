import unittest

from latejoin import join


class LatejoinTest(unittest.TestCase):
    def test_join_latest(self):
        facts = [{"id": 1, "v": 10}]
        dims = [{"id": 1, "name": "old"}, {"id": 1, "name": "new"}]
        self.assertEqual(join(facts, dims)[0]["dim"]["name"], "new")

    def test_missing_dim(self):
        self.assertIsNone(join([{"id": 2}], [])[0]["dim"])

    def test_event_time_multi_version(self):
        facts = [
            {"id": 1, "event_time": "2024-01-15", "v": 10},
            {"id": 1, "event_time": "2024-03-05", "v": 20},
        ]
        dims = [
            {"id": 1, "effective_time": "2024-01-01", "name": "v1"},
            {"id": 1, "effective_time": "2024-02-01", "name": "v2"},
        ]
        rows = join(facts, dims)
        self.assertEqual(rows[0]["dim"]["name"], "v1")
        self.assertEqual(rows[1]["dim"]["name"], "v2")
        self.assertTrue(all(row["matched"] for row in rows))

    def test_fact_before_first_version_unmatched(self):
        facts = [{"id": 1, "event_time": "2023-12-31"}]
        dims = [{"id": 1, "effective_time": "2024-01-01", "name": "v1"}]
        row = join(facts, dims)[0]
        self.assertIsNone(row["dim"])
        self.assertFalse(row["matched"])

    def test_late_dim_backfill(self):
        facts = [
            {"id": 1, "event_time": "2024-01-15", "v": 10},
            {"id": 1, "event_time": "2024-03-05", "v": 20},
        ]
        dims = [
            {"id": 1, "effective_time": "2024-03-01", "name": "v2"},
            {"id": 1, "effective_time": "2024-01-01", "name": "v1"},  # 迟到
        ]
        result = join(facts, dims)
        self.assertEqual(result[0]["dim"]["name"], "v1")
        self.assertTrue(result[0]["backfilled"])
        self.assertEqual(result[1]["dim"]["name"], "v2")
        self.assertFalse(result[1]["backfilled"])
        self.assertEqual(result.stats["backfilled"], 1)
        batches = result.stats["backfill_batches"]
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]["dim"]["name"], "v1")
        self.assertEqual(batches[0]["affected"], 1)
        self.assertEqual(batches[0]["facts"], [facts[0]])

    def test_late_dim_rewrites_existing_match(self):
        facts = [{"id": 1, "event_time": "2024-02-15", "v": 10}]
        dims = [
            {"id": 1, "effective_time": "2024-01-01", "name": "v1"},
            {"id": 1, "effective_time": "2024-03-01", "name": "v3"},
            {"id": 1, "effective_time": "2024-02-01", "name": "v2"},  # 迟到
        ]
        result = join(facts, dims)
        self.assertEqual(result[0]["dim"]["name"], "v2")
        self.assertTrue(result[0]["backfilled"])
        self.assertEqual(result.stats["backfilled"], 1)

    def test_null_key_kept_and_marked(self):
        facts = [{"id": None, "v": 10}, {"v": 20}]
        rows = join(facts, [{"id": 1, "name": "a"}])
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertIsNone(row["dim"])
            self.assertFalse(row["matched"])
            self.assertFalse(row["backfilled"])

    def test_stats(self):
        facts = [
            {"id": 1, "event_time": "2024-01-15"},
            {"id": 2},
            {"id": None},
        ]
        dims = [
            {"id": 1, "effective_time": "2024-02-01", "name": "v2"},
            {"id": 1, "effective_time": "2024-01-01", "name": "v1"},  # 迟到
        ]
        result = join(facts, dims)
        stats = result.stats
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["matched"], 1)
        self.assertEqual(stats["unmatched"], 2)
        self.assertAlmostEqual(stats["match_rate"], 1 / 3)
        self.assertEqual(stats["backfilled"], 1)
        self.assertEqual(len(stats["unmatched_samples"]), 2)
        self.assertIn({"id": None}, stats["unmatched_samples"])
