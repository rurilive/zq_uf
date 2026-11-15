import unittest

import backfill2
from backfill2 import apply, export, plan, query, replay, retry

ROWS = [{"time": t, "channel": c, "value": v}
        for t in range(1, 11) for c, v in [("a", t), ("b", 2 * t)]]


def fresh(rows=ROWS, **kw):
    backfill2.backfill._TARGET.clear()
    return plan({"start": 1, "end": 10}, slice_size=2,
                dimensions={"channel": ["a", "b"]}, **kw)


class BackfillTest(unittest.TestCase):
    def test_slices_by_time_and_dimension(self):
        p = fresh()
        self.assertEqual(len(p["slices"]), 10)  # 5 个时间片 × 2 个维度
        self.assertTrue(p["saved"])

    def test_retract_before_write_no_doubling(self):
        p = fresh()
        apply(p, ROWS)
        run2 = apply(p, ROWS)  # 重复回填：先撤销再写
        self.assertGreater(run2["retracted"], 0)
        total = sum(t["metric"] for t in backfill2.backfill._TARGET.values())
        self.assertEqual(total, sum(r["value"] for r in ROWS))

    def test_trial_run_verifies_no_doubling(self):
        run = apply(fresh(), ROWS, trial=True)
        self.assertFalse(run["double_check"]["doubled"])

    def test_stop_when_diff_over_threshold(self):
        p = fresh(threshold=0.01)
        apply(p, ROWS)
        changed = [dict(r, value=r["value"] * 10) for r in ROWS]
        run = apply(p, changed)
        self.assertTrue(run["stopped"])
        self.assertIn("对账差异超阈值", run["stop_reason"])

    def test_failed_slice_records_reason_and_retry(self):
        p = fresh()
        bad = [{"time": 1, "channel": "a", "value": 1}, {"broken": True}]
        run = apply(p, bad, compute=lambda rows: 1 / 0 if len(rows) == 1 else 0)
        failed = [s for s in run["slices"] if s["status"] == "failed"]
        self.assertTrue(all(s["reason"] for s in failed))
        r2 = retry(run["run_id"])
        self.assertEqual(len(r2["slices"]), len(failed))

    def test_plan_reuse_query_export_replay(self):
        p = fresh()
        run = apply(p, ROWS)
        self.assertEqual(query(run["run_id"])["stopped"], False)
        self.assertIn("checksum_after", export(run["run_id"]))
        again = replay(run["run_id"])
        self.assertEqual(again["diff"]["rows_after"], run["diff"]["rows_after"])


if __name__ == "__main__":
    unittest.main()
