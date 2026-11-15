import unittest

from tablesample import error, sample


class SampleTest(unittest.TestCase):
    def test_sample_size_follows_ratio(self):
        rows = [{"value": index} for index in range(100)]
        self.assertEqual(len(sample(rows, {"ratio": 0.2})["rows"]), 20)

    def test_sample_has_no_seed(self):
        self.assertIsNone(sample([{"value": 1}], {"ratio": 1.0})["seed"])

    def test_error_has_no_range(self):
        self.assertIsNone(error({"rows": [{"value": 1}]})["range"])


class StratifiedSampleTest(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"id": index, "grp": "a" if index < 80 else "b", "value": float(index)}
            for index in range(100)
        ]

    def test_stratified_counts_and_tags(self):
        result = sample(self.rows, {"stratify_by": "grp", "ratio": 0.5, "seed": 7})
        self.assertEqual(result["strata"]["a"]["population"], 80)
        self.assertEqual(result["strata"]["a"]["sampled"], 40)
        self.assertEqual(result["strata"]["b"]["sampled"], 10)
        self.assertTrue(all("_stratum" in row for row in result["rows"]))
        self.assertEqual({row["_stratum"] for row in result["rows"]}, {"a", "b"})

    def test_fixed_seed_reproducible(self):
        plan = {"stratify_by": "grp", "ratio": 0.3, "seed": 42}
        self.assertEqual(sample(self.rows, plan)["rows"], sample(self.rows, plan)["rows"])

    def test_seed_derived_from_query(self):
        first = sample(self.rows, {"stratify_by": "grp", "ratio": 0.2, "query": "q1"})["plan"]
        again = sample(self.rows, {"stratify_by": "grp", "ratio": 0.2, "query": "q1"})["plan"]
        other = sample(self.rows, {"stratify_by": "grp", "ratio": 0.2, "query": "q2"})["plan"]
        self.assertEqual(first["seed"], again["seed"])
        self.assertNotEqual(first["seed"], other["seed"])

    def test_ratio_and_size_mix(self):
        plan = {"stratify_by": "grp", "ratio": 0.1, "strata": {"b": {"size": 5}}, "seed": 1}
        result = sample(self.rows, plan)
        self.assertEqual(result["strata"]["a"]["sampled"], 8)
        self.assertEqual(result["strata"]["b"]["sampled"], 5)

    def test_replay_with_same_plan(self):
        first = sample(self.rows, {"stratify_by": "grp", "ratio": 0.25, "query": "replay"})
        second = sample(self.rows, first["plan"])
        self.assertEqual(first["rows"], second["rows"])

    def test_error_range_by_field_and_confidence(self):
        picked = sample(self.rows, {"ratio": 0.5, "seed": 3})
        stats = error(picked)["fields"]["value"]
        low, high = stats["error_range"]
        self.assertLessEqual(low, stats["mean"])
        self.assertLessEqual(stats["mean"], high)
        self.assertIn("p50", stats["quantiles"])
        wider = error({**picked, "confidence": 0.99})["fields"]["value"]
        self.assertGreater(wider["error"], stats["error"])

    def test_long_tail_reported_separately(self):
        rows = [{"value": index % 10} for index in range(95)]
        rows += [{"value": 1000 + index} for index in range(5)]
        picked = sample(rows, {"stratify_by": "value", "boundaries": [10], "size": 5, "seed": 9})
        report = error(picked)
        self.assertIn(">=10", report["strata"])
        self.assertIn(">=10", report["long_tail"])
        self.assertNotIn("<10", report["long_tail"])

    def test_insufficient_sample_warning(self):
        rows = [{"grp": "a", "value": 1.0}, {"grp": "b", "value": 2.0}]
        picked = sample(rows, {"stratify_by": "grp", "ratio": 1.0, "seed": 5})
        self.assertTrue(all(info["warning"] for info in picked["strata"].values()))
        self.assertTrue(error(picked)["warnings"])
