import json
import unittest

import sitehealth
from sitehealth import (
    daily,
    export,
    export_watchlist,
    history,
    replay,
    report,
    score,
    watchlist,
)

T0 = 1_700_000_000.0
DAY = 86400.0


class HealthTest(unittest.TestCase):
    def setUp(self):
        sitehealth.reset()

    def _fail(self, site, n, ts=T0):
        snap = None
        for i in range(n):
            snap = report(site, {"ok": False, "status": 503}, ts=ts + i * 60)
        return snap

    def _recover(self, site, days, ts=T0):
        snap = None
        for d in range(days):
            for i in range(12):
                snap = report(site, {"ok": True, "rtt": 0.2},
                              ts=ts + (d + 1) * DAY + i * 300)
        return snap

    def test_parts_and_total(self):
        snap = report("a", {"ok": True, "rtt": 0.2}, ts=T0)
        self.assertEqual(set(snap["parts"]),
                         {"success", "latency", "throttle", "content"})
        self.assertAlmostEqual(snap["score"], sum(
            snap["parts"][k] * w
            for k, w in zip(("success", "latency", "throttle", "content"),
                            (0.4, 0.2, 0.2, 0.2))), places=3)

    def test_consecutive_failures_drop_fast(self):
        snap = self._fail("a", 3)
        self.assertLess(snap["score"], 0.5)
        self.assertAlmostEqual(snap["parts"]["success"], 0.0, places=2)
        self.assertEqual(snap["trend"], "down")

    def test_recovery_is_slow(self):
        self._fail("a", 3)
        before = score("a")["score"]
        after = report("a", {"ok": True, "rtt": 0.2}, ts=T0 + 600)["score"]
        self.assertGreater(after, before)
        self.assertLess(after - before, 0.15)  # 单次恢复只小幅回升

    def test_recent_outweighs_old_history(self):
        for i in range(30):  # 三天前的成功不该稀释最近的故障
            report("a", {"ok": True, "rtt": 0.1}, ts=T0 - 3 * DAY + i * 60)
        snap = self._fail("a", 3)
        self.assertLess(snap["parts"]["success"], 0.1)

    def test_throttle_part_and_reason(self):
        snap = report("a", {"ok": True, "status": 429}, ts=T0)
        self.assertLess(snap["parts"]["throttle"], 1.0)
        self.assertIn("限流", snap["last_change"]["reason"])

    def test_content_anomaly_part(self):
        snap = report("a", {"ok": True, "content_ok": False}, ts=T0)
        self.assertLess(snap["parts"]["content"], 1.0)
        self.assertIn("内容异常", snap["last_change"]["reason"])

    def test_slow_latency_part(self):
        snap = report("a", {"ok": True, "rtt": 4.0}, ts=T0)
        self.assertLess(snap["parts"]["latency"], 1.0)
        self.assertIn("响应缓慢", snap["last_change"]["reason"])

    def test_watchlist_enter_and_exit_after_recovery_days(self):
        snap = self._fail("a", 4)
        self.assertEqual(snap["dispatch"], "watch")
        self.assertEqual([w["site"] for w in watchlist()], ["a"])
        snap = self._recover("a", 2)  # 两天恢复还不够
        self.assertEqual(snap["dispatch"], "watch")
        snap = self._recover("a", 1, ts=T0 + 2 * DAY)
        self.assertEqual(snap["dispatch"], "normal")
        self.assertEqual(watchlist(), [])
        actions = [r["action"] for r in export("a")["watch_log"]]
        self.assertEqual(actions, ["enter", "exit"])

    def test_history_by_day_and_reason(self):
        self._fail("a", 3)
        day = "2023-11-14"  # T0 当天（UTC）
        records = history("a", day=day)
        self.assertEqual(len(records), 3)
        self.assertTrue(all(r["reason"] for r in records))
        self.assertIsNotNone(score("a")["last_change"]["reason"])

    def test_daily_summary(self):
        self._fail("a", 3)
        self._recover("a", 1)
        days = daily("a")
        self.assertEqual(len(days), 2)
        self.assertEqual(days[0]["changes"], 3)
        self.assertLess(days[0]["last"], days[1]["last"])

    def test_replay_matches_live(self):
        self._fail("a", 4)
        self._recover("a", 2)
        self.assertEqual(replay("a")["final"]["score"], score("a")["score"])
        self.assertEqual(len(replay("a")["history"]), len(history("a")))

    def test_export_is_json_serializable(self):
        self._fail("a", 4)
        self._recover("a", 3)
        json.dumps(export("a"))
        json.dumps(export_watchlist())

    def test_unknown_site_defaults_full_score(self):
        snap = score("nobody")
        self.assertEqual(snap["score"], 1.0)
        self.assertEqual(snap["dispatch"], "normal")


if __name__ == "__main__":
    unittest.main()
