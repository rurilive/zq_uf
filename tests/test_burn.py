import tempfile
import unittest

import proxyburn
from proxyburn import burn, store
from proxyburn import CONFIG, history, report, status, use

_DEFAULT_CONFIG = dict(CONFIG)


class BurnTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        store.STATE_DIR = self._dir.name
        CONFIG.clear()
        CONFIG.update(_DEFAULT_CONFIG)
        CONFIG["low_traffic_hours"] = None  # 测试默认不限制低流量窗口
        self._now = 1_000_000.0
        burn._now = lambda: self._now

    def tearDown(self):
        self._dir.cleanup()

    def trip(self, proxy="p1"):
        for _ in range(CONFIG["failure_threshold"]):
            report(proxy, False)

    def test_single_failure_does_not_trip(self):
        r = report("p1", False)
        self.assertFalse(r["tripped"])
        self.assertEqual(r["state"], "closed")
        self.assertEqual(r["failures"], 1)
        self.assertTrue(use("p1")["usable"])

    def test_success_resets_failure_count(self):
        report("p1", False)
        report("p1", True)
        report("p1", False)
        self.assertEqual(status("p1")["failures"], 1)

    def test_consecutive_failures_trip(self):
        r = self.trip_result = report("p1", False)
        report("p1", False)
        r = report("p1", False)
        self.assertTrue(r["tripped"])
        self.assertEqual(r["state"], "open")

    def test_open_blocks_new_tasks(self):
        self.trip()
        r = use("p1")
        self.assertFalse(r["usable"])
        self.assertEqual(r["state"], "open")

    def test_probe_waits_for_interval(self):
        self.trip()
        self.assertFalse(use("p1")["usable"])
        self._now += CONFIG["probe_interval_sec"]
        r = use("p1")
        self.assertTrue(r["usable"])
        self.assertTrue(r["probe"])
        self.assertEqual(r["state"], "half_open")
        self.assertEqual(r["recovery_stage"], 1)

    def test_probe_only_in_low_traffic_window(self):
        CONFIG["low_traffic_hours"] = None
        self.trip()
        self._now += CONFIG["probe_interval_sec"]
        # 把探测窗口设到当前 UTC 小时之外
        import time
        hour = time.gmtime(self._now).tm_hour
        CONFIG["low_traffic_hours"] = ((hour + 1) % 24, (hour + 2) % 24)
        self.assertFalse(use("p1")["usable"])
        CONFIG["low_traffic_hours"] = None
        self.assertTrue(use("p1")["usable"])

    def test_half_open_limits_inflight_by_stage(self):
        self.trip()
        self._now += CONFIG["probe_interval_sec"]
        self.assertTrue(use("p1")["usable"])  # 第 1 级只允许 1 个在途
        self.assertFalse(use("p1")["usable"])

    def test_failed_probe_returns_to_open(self):
        self.trip()
        self._now += CONFIG["probe_interval_sec"]
        use("p1")
        r = report("p1", False)
        self.assertEqual(r["state"], "open")
        self.assertEqual(r["probe_result"], "failed")
        self.assertFalse(use("p1")["usable"])

    def test_staged_recovery_not_full_at_once(self):
        self.trip()
        self._now += CONFIG["probe_interval_sec"]
        for stage in (1, 2):
            for _ in range(CONFIG["successes_per_stage"]):
                self.assertEqual(use("p1")["recovery_stage"], stage)
                r = report("p1", True)
            if stage < CONFIG["recovery_stages"]:
                self.assertEqual(r["state"], "half_open")
                self.assertEqual(r["recovery_stage"], stage + 1)
        self.assertEqual(r["state"], "closed")
        self.assertEqual(r["recovery_stage"], 0)
        self.assertTrue(use("p1")["usable"])

    def test_response_fields(self):
        r = report("p1", False)
        for key in ("state", "failures", "probe", "probe_result",
                    "recovery_stage", "version"):
            self.assertIn(key, r)

    def test_version_increments(self):
        v0 = status("p1")["version"]
        report("p1", False)
        report("p1", False)
        self.assertGreater(status("p1")["version"], v0 + 1)

    def test_audit_history_per_proxy(self):
        self.trip("p1")
        self.trip("p2")
        events = [e["event"] for e in history("p1")]
        self.assertEqual(events, ["tripped"])
        self.assertEqual(len(history("p2")), 1)

    def test_state_shared_across_processes(self):
        # 状态落盘，另一"进程"（重新读文件）看到一致状态
        self.trip("p1")
        data = {}
        with store.locked() as d:
            data.update(d)
        self.assertEqual(data["p1"]["state"], "open")


if __name__ == "__main__":
    unittest.main()
