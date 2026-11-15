import unittest

from skewwatch import configure, history, observe, replay, report

SLOW = {
    "name": "slow", "elapsed": 600,
    "partitions": [
        {"id": 0, "rows": 1000, "elapsed": 20, "keys": {"a": 600, "b": 400}},
        {"id": 1, "rows": 1100, "elapsed": 22, "keys": {"c": 1100}},
        {"id": 2, "rows": 12000, "elapsed": 480, "keys": {"vip": 11500, "d": 500}},
        {"id": 3, "rows": 900, "elapsed": 18, "keys": {"e": 900}},
    ],
}


class SkewTest(unittest.TestCase):
    def setUp(self):
        configure(threshold=3.0, period=0.0)

    def test_observe_never_skewed(self):
        self.assertFalse(observe({"name": "t1", "elapsed": 100})["skewed"])

    def test_report_ratio_one(self):
        self.assertEqual(report("t1")["ratio"], 1.0)

    def test_no_hot_keys(self):
        self.assertEqual(report("t1")["hot"], [])

    def test_detects_skew(self):
        r = observe(dict(SLOW, name="skew1"))
        self.assertTrue(r["skewed"])
        self.assertGreaterEqual(r["ratio"], 3.0)

    def test_hot_key_matches_actual_partition(self):
        r = observe(dict(SLOW, name="skew2"))
        self.assertEqual(r["hot"][0]["key"], "vip")
        self.assertEqual(r["hot"][0]["partitions"], [2])
        self.assertEqual(r["hot_partitions"][0]["partition"], 2)

    def test_hot_sorted_by_ratio(self):
        task = dict(SLOW, name="skew3")
        task["partitions"] = SLOW["partitions"] + [
            {"id": 4, "rows": 6000, "elapsed": 200, "keys": {"warm": 6000}}]
        ratios = [h["ratio"] for h in observe(task)["hot"]]
        self.assertEqual(ratios, sorted(ratios, reverse=True))

    def test_advice_has_salt_or_split_with_benefit(self):
        r = observe(dict(SLOW, name="skew4"))
        types = {a["type"] for a in r["advice"]}
        self.assertIn("salt", types)
        self.assertIn("split", types)
        self.assertTrue(all(a["est_benefit"] > 0 for a in r["advice"]))

    def test_rerun_keeps_history(self):
        observe(dict(SLOW, name="rerun"))
        observe(dict(SLOW, name="rerun", elapsed=610))
        self.assertEqual(report("rerun")["runs"], 2)
        self.assertEqual(len(history("rerun")), 2)
        self.assertEqual(history("rerun")[-1]["elapsed"], 610)

    def test_period_reuses_last_detection(self):
        configure(period=60)
        observe(dict(SLOW, name="periodic", time=1000))
        cached = observe(dict(SLOW, name="periodic", time=1030))
        self.assertTrue(cached["cached"])
        self.assertEqual(report("periodic")["runs"], 1)

    def test_replay_matches_history(self):
        observe(dict(SLOW, name="rep"))
        observe(dict(SLOW, name="rep", elapsed=610))
        replayed = replay("rep")
        self.assertEqual(len(replayed), 2)
        self.assertEqual([r["elapsed"] for r in replayed], [600, 610])
        self.assertTrue(all(r["skewed"] for r in replayed))


if __name__ == "__main__":
    unittest.main()
