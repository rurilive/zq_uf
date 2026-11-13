import json
import math
import unittest

from logsample import (
    begin_batch,
    enable_sampling,
    estimate,
    get_rate,
    lines,
    replay,
    reset,
    sampling_off,
    set_rate,
    stats,
    write,
)


class SampleTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_writes_every_record(self):
        for index in range(20):
            write({"url": f"/{index}", "status": 200})
        self.assertEqual(len(lines()), 20)

    def test_errors_not_special(self):
        write({"url": "/1", "status": 500})
        write({"url": "/2", "status": 200})
        self.assertEqual(len(lines()), 2)

    def test_no_sampling_fields(self):
        row = write({"url": "/1", "status": 200})
        self.assertNotIn("sampled", row)


class TieredSamplingTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_ratio_sampling(self):
        enable_sampling(0.25)
        for index in range(200):
            write({"url": f"/{index}", "status": 200})
        self.assertEqual(len(lines()), 50)

    def test_errors_and_slow_fully_kept(self):
        enable_sampling(0.1)
        for index in range(100):
            write({"url": f"/{index}", "status": 200})
        write({"url": "/err", "status": 500})
        write({"url": "/slow", "status": 200, "slow": True})
        self.assertEqual(len(lines()), 12)
        reasons = {row["url"]: row["sample_reason"]
                   for row in map(json.loads, lines())}
        self.assertEqual(reasons["/err"], "error")
        self.assertEqual(reasons["/slow"], "slow")

    def test_dynamic_rate_by_site_and_hour(self):
        enable_sampling(0.1)
        set_rate(0.5, site="shop", hour=9)
        self.assertEqual(get_rate("shop", 9), 0.5)
        self.assertEqual(get_rate("shop", 10), 0.1)
        self.assertEqual(get_rate("blog", 9), 0.1)
        for index in range(100):
            write({"site": "shop", "hour": 9, "url": f"/{index}", "status": 200})
            write({"site": "shop", "hour": 10, "url": f"/{index}", "status": 200})
        self.assertEqual(stats("shop")["shop"]["kept"], 50 + 10)

    def test_estimate_restores_total(self):
        enable_sampling(0.5)
        for index in range(400):
            write({"site": "shop", "url": f"/{index}", "status": 200})
        for index in range(20):
            write({"site": "shop", "url": f"/e{index}", "status": 500})
        est = estimate("shop")["shop"]
        self.assertEqual(est["total"], 420)
        self.assertEqual(est["estimated_total"], 420.0)
        self.assertEqual(est["error"], 20.0)
        self.assertEqual(est["rate"], 0.5)

    def test_estimate_error_matches_binomial(self):
        enable_sampling(0.5)
        for index in range(100):
            write({"site": "blog", "url": f"/{index}", "status": 200})
        est = estimate("blog")["blog"]
        self.assertEqual(est["estimated_total"], 100.0)
        self.assertEqual(est["error"], math.sqrt(50 * 0.5 / 0.25))

    def test_decision_written_into_record(self):
        enable_sampling(1.0)
        row = write({"url": "/1", "status": 200})
        self.assertTrue(row["sampled"])
        self.assertEqual(row["sample_reason"], "sampled")
        self.assertEqual(row["sample_rate"], 1.0)
        self.assertIn("batch", row)

    def test_replay_by_batch(self):
        enable_sampling(0.5)
        for index in range(10):
            write({"url": f"/a{index}", "status": 200})
        batch = begin_batch()
        for index in range(10):
            write({"url": f"/b{index}", "status": 200})
        replayed = replay(batch)
        self.assertEqual(len(replayed), 5)
        self.assertTrue(all(row["sampled"] for row in replayed))
        self.assertTrue(all(row["batch"] == batch for row in replayed))
        self.assertEqual(len(replay(0)), 5)

    def test_sampling_off_for_abnormal_batch(self):
        enable_sampling(0.1)
        begin_batch()
        with sampling_off():
            for index in range(30):
                write({"url": f"/{index}", "status": 200})
        self.assertEqual(len(lines()), 30)
        row = write({"url": "/after", "status": 200})
        self.assertIn("sampled", row)
