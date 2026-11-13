import unittest

from siteweight import SITES, Scheduler, feedback, next_site, reset


class WeightTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_round_robin(self):
        picked = [next_site() for _ in range(6)]
        self.assertEqual(picked, SITES * 2)

    def test_feedback_does_not_reorder(self):
        feedback("shop", False)
        feedback("shop", False)
        self.assertEqual(next_site(), "shop")

    def test_all_sites_equal(self):
        counts = {site: 0 for site in SITES}
        for _ in range(30):
            counts[next_site()] += 1
        self.assertEqual(set(counts.values()), {10})


class DynamicWeightTest(unittest.TestCase):
    def test_success_and_updates_raise_weight(self):
        sched = Scheduler(["a", "b"], min_samples=3)
        for _ in range(3):
            sched.feedback("a", {"ok": True, "updated": True})
            sched.feedback("b", {"ok": False})
        self.assertGreater(sched.stats("a")["weight"],
                           sched.stats("b")["weight"])

    def test_min_samples_threshold(self):
        sched = Scheduler(["a"], min_samples=3)
        sched.feedback("a", False)
        sched.feedback("a", False)
        self.assertEqual(sched.stats("a")["weight"], 1.0)

    def test_consecutive_failures_demote_then_recover(self):
        sched = Scheduler(["a"], min_samples=2, decay=0.5)
        sched.feedback("a", True)
        sched.feedback("a", True)
        before = sched.stats("a")["weight"]
        sched.feedback("a", False)
        sched.feedback("a", False)
        demoted = sched.stats("a")["weight"]
        self.assertLess(demoted, before)
        sched.feedback("a", True)
        self.assertGreater(sched.stats("a")["weight"], demoted)

    def test_guarantee_quota_prevents_starvation(self):
        sched = Scheduler(["a", "b", "c"], guarantee_window=4)
        for _ in range(3):
            sched.feedback("a", {"ok": True, "updated": True})
            sched.feedback("b", {"ok": True, "updated": True})
            sched.feedback("c", {"ok": False})
        counts = {site: 0 for site in ("a", "b", "c")}
        for _ in range(40):
            counts[sched.next()] += 1
        self.assertGreaterEqual(counts["c"], 40 // 4 - 1)
        self.assertLessEqual(sched.stats("c")["max_wait"], 4)

    def test_wait_stats_reported(self):
        sched = Scheduler(["a", "b"])
        for _ in range(10):
            sched.next()
        stats = sched.stats("a")
        self.assertEqual(stats["picks"], 5)
        self.assertEqual(stats["max_wait"], 2)
        self.assertAlmostEqual(stats["avg_wait"], 2.0)

    def test_weight_log_records_changes(self):
        sched = Scheduler(["a"], min_samples=1)
        sched.feedback("a", {"ok": True, "updated": True})
        self.assertEqual(len(sched.weight_log), 1)
        entry = sched.weight_log[0]
        self.assertEqual(entry["site"], "a")
        self.assertEqual(entry["old"], 1.0)
        self.assertGreater(entry["new"], entry["old"])


class ErrorPathTest(unittest.TestCase):
    def test_feedback_unknown_site(self):
        sched = Scheduler(["a"])
        with self.assertRaises(KeyError):
            sched.feedback("ghost", True)

    def test_feedback_bad_result_type(self):
        sched = Scheduler(["a"])
        with self.assertRaises(TypeError):
            sched.feedback("a", "yes")

    def test_feedback_dict_missing_ok(self):
        sched = Scheduler(["a"])
        with self.assertRaises(ValueError):
            sched.feedback("a", {"updated": True})

    def test_empty_sites_rejected(self):
        with self.assertRaises(ValueError):
            Scheduler([])

    def test_duplicate_sites_rejected(self):
        with self.assertRaises(ValueError):
            Scheduler(["a", "a"])

    def test_invalid_thresholds_rejected(self):
        with self.assertRaises(ValueError):
            Scheduler(["a"], decay=0)
        with self.assertRaises(ValueError):
            Scheduler(["a"], min_weight=0)
        with self.assertRaises(ValueError):
            Scheduler(["a", "b", "c"], guarantee_window=2)

    def test_stats_unknown_site(self):
        sched = Scheduler(["a"])
        with self.assertRaises(KeyError):
            sched.stats("ghost")
