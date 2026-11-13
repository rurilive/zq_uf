import unittest

from logsample import lines, reset, write


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

import json

from logsample import (
    pause,
    replay,
    resume,
    set_rate,
    set_seed,
    set_target,
    stats,
    write_batch,
)


def normals(site, count, hour=9):
    return [{"site": site, "url": "/%d" % i, "status": 200, "hour": hour}
            for i in range(count)]


class TieredSamplingTest(unittest.TestCase):
    def setUp(self):
        reset()
        set_seed(42)

    def test_ratio_sampling(self):
        set_rate("shop", 0.1)
        for record in normals("shop", 2000):
            write(record)
        kept = lines()
        self.assertTrue(150 < len(kept) < 250)
        row = json.loads(kept[0])
        self.assertTrue(row["sampled"])
        self.assertEqual(row["sample_rate"], 0.1)
        self.assertEqual(row["sample_reason"], "rate")

    def test_errors_and_slow_always_kept(self):
        set_rate("shop", 0.01)
        for i in range(50):
            write({"site": "shop", "status": 500, "hour": 9})
            write({"site": "shop", "status": 200, "slow": True, "hour": 9})
        for record in normals("shop", 500):
            write(record)
        kept = [json.loads(line) for line in lines()]
        errors = [r for r in kept if r["status"] == 500]
        slows = [r for r in kept if r.get("slow")]
        self.assertEqual(len(errors), 50)
        self.assertEqual(len(slows), 50)
        self.assertEqual({r["sample_reason"] for r in errors}, {"error"})
        self.assertEqual({r["sample_reason"] for r in slows}, {"slow"})

    def test_dynamic_rate_by_hour_and_target(self):
        set_rate("shop", 0.5, hour=9)
        set_rate("shop", 0.05, hour=10)
        for record in normals("shop", 1000, hour=9):
            write(record)
        for record in normals("shop", 1000, hour=10):
            write(record)
        info = stats("shop")
        self.assertEqual(info["shop@09"]["rate"], 0.5)
        self.assertEqual(info["shop@10"]["rate"], 0.05)
        self.assertGreater(info["shop@09"]["kept"], info["shop@10"]["kept"])

        reset()
        set_seed(7)
        set_target("news", 100)
        for record in normals("news", 2000):
            write(record)
        kept = stats("news")["news@09"]["kept"]
        self.assertTrue(300 < kept < 500)  # 约 target*(1+ln(N/target))

    def test_total_estimation_with_error(self):
        set_rate("shop", 0.2)
        for record in normals("shop", 1000):
            write(record)
        for _ in range(30):
            write({"site": "shop", "status": 500, "hour": 9})
        info = stats("shop")["shop@09"]
        self.assertEqual(info["seen"], 1030)          # 精确计数
        self.assertAlmostEqual(info["estimated_total"], 1030, delta=3 * info["error"])
        self.assertGreater(info["error"], 0)

    def test_batch_replay_and_keep_all(self):
        set_rate("shop", 0.1)
        batch_id = write_batch(normals("shop", 200), batch_id="b1")
        kept_lines = [line for line in lines() if '"batch": "b1"' in line]
        self.assertEqual(replay(batch_id), kept_lines)

        abnormal = normals("shop", 50, hour=10)
        write_batch(abnormal, batch_id="b2", keep_all=True)
        self.assertEqual(len(replay("b2")), 50)       # 异常批次全量保留
        row = json.loads(replay("b2")[0])
        self.assertEqual(row["sample_reason"], "paused")

    def test_pause_and_resume(self):
        set_rate("shop", 0.1)
        pause()
        for record in normals("shop", 100):
            write(record)
        self.assertEqual(len(lines()), 100)
        resume()
        reset()
        set_seed(42)
        set_rate("shop", 0.1)
        for record in normals("shop", 1000):
            write(record)
        self.assertLess(len(lines()), 200)
