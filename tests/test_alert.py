import unittest

from metricalert import emit, resolve
from metricalert import alert


class AlertTest(unittest.TestCase):
    def setUp(self):
        alert._sent.clear()

    def test_emit_counts(self):
        emit({"job": "j1"}); self.assertEqual(emit({"job": "j2"}), 2)

    def test_resolve_ok(self):
        self.assertTrue(resolve("j1")["resolved"])

    def test_no_aggregation(self):
        emit({"job": "j1"})
        self.assertEqual(len(alert._sent), 1)


class FakeClock(object):
    def __init__(self, start=1000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


class AggregationTest(unittest.TestCase):
    def setUp(self):
        alert._sent.clear()
        self.clock = FakeClock()
        alert.configure(window_seconds=60, clock=self.clock)

    def tearDown(self):
        alert.configure(window_seconds=alert.WINDOW_SECONDS, clock=None)

    def _emit(self, job, reason="upstream", ts=None, **extra):
        payload = dict(extra)
        payload["job"] = job
        payload["reason"] = reason
        payload["ts"] = self.clock.now if ts is None else ts
        return emit(payload)

    def test_same_root_cause_sends_one(self):
        self._emit("checkout-1")
        self._emit("checkout-2")
        self._emit("checkout-3")
        messages = [m for m in alert._sent if m.get("type") == "aggregate"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["affected_jobs"], 3)

    def test_aggregate_key_is_root_cause_and_job_group(self):
        self._emit("checkout-1")
        self._emit("checkout-2")
        aggregate = alert.aggregates()[0]
        self.assertEqual(aggregate["key"], "upstream|checkout")
        self.assertEqual(aggregate["root_cause"], "upstream")
        self.assertEqual(aggregate["job_group"], "checkout")

    def test_aggregate_resets_next_window(self):
        self._emit("checkout-1")
        self.clock.advance(60)
        self._emit("checkout-2")
        self.assertEqual(len(alert.aggregates()), 2)

    def test_dimension_splits_windows(self):
        self._emit("checkout-1", dimension="latency")
        self._emit("checkout-2", dimension="errors")
        keys = [a["key"] for a in alert.aggregates()]
        self.assertEqual(keys, ["upstream|checkout|latency", "upstream|checkout|errors"])
        self.assertEqual(len(alert._sent), 2)

    def test_different_root_cause_not_merged(self):
        self._emit("checkout-1", reason="upstream")
        self._emit("checkout-2", reason="disk")
        self.assertEqual(len(alert.aggregates()), 2)

    def test_impact_statistics(self):
        alert.configure(window_seconds=120, clock=self.clock)
        self._emit("checkout-1", ts=1001)
        self.clock.now = 1020
        self._emit("checkout-2")
        self.clock.now = 1045
        self._emit("checkout-3")
        self._emit("checkout-1")
        aggregate = alert.aggregates()[0]
        self.assertEqual(aggregate["count"], 4)
        self.assertEqual(aggregate["affected_jobs"], 3)
        self.assertEqual(aggregate["first_ts"], 1001)
        self.assertEqual(aggregate["last_ts"], 1045)
        self.assertEqual(aggregate["jobs"], ["checkout-1", "checkout-2", "checkout-3"])

    def test_duplicate_in_window_is_suppressed(self):
        self._emit("checkout-1")
        self.clock.now = 1010
        self._emit("checkout-2")
        self.assertEqual(len(alert.suppressions()), 1)
        self.assertEqual(alert.suppressions()[0]["reason"], "window")
        self.assertEqual(alert.suppressions()[0]["key"], "upstream|checkout")

    def test_recovery_summary(self):
        self._emit("checkout-1")
        self.clock.now = 1005
        self._emit("checkout-2")
        result = resolve("upstream|checkout", now=1030)
        self.assertTrue(result["resolved"])
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["affected_jobs"], 2)
        self.assertEqual(result["count"], 2)
        recovery = alert.recoveries()[0]
        self.assertEqual(recovery["key"], "upstream|checkout")
        self.assertEqual(recovery["affected_jobs"], 2)
        self.assertEqual(recovery["first_ts"], 1000)
        self.assertEqual(recovery["last_ts"], 1005)

    def test_recovery_after_old_resolve_leaves_nothing_open(self):
        self._emit("checkout-1")
        resolve("upstream|checkout")
        self.clock.advance(60)
        emit({"job": "checkout-2", "reason": "upstream", "ts": 1060})
        self.assertEqual(len(alert.aggregates()), 2)

    def test_resolve_twice_is_idempotent(self):
        self._emit("checkout-1")
        self.assertEqual(len(resolve("upstream|checkout")["recoveries"]), 1)
        self.assertEqual(len(resolve("upstream|checkout")["recoveries"]), 0)


class SilenceTest(unittest.TestCase):
    def setUp(self):
        alert._sent.clear()
        self.clock = FakeClock()
        alert.configure(window_seconds=60, clock=self.clock)

    def tearDown(self):
        alert.configure(window_seconds=alert.WINDOW_SECONDS, clock=None)

    def test_silence_suppresses_and_reports_until(self):
        alert.silence("upstream|checkout", seconds=30, now=1000)
        emit({"job": "checkout-1", "reason": "upstream", "ts": 1000})
        emit({"job": "checkout-2", "reason": "upstream", "ts": 1005})
        self.assertEqual(len(alert._sent), 0)
        self.assertEqual(len(alert.suppressions()), 2)
        record = alert.suppressions()[0]
        self.assertEqual(record["reason"], "silence")
        self.assertEqual(record["silent_until"], 1030)

    def test_silence_with_silent_until_in_alert(self):
        emit({"job": "checkout-1", "reason": "upstream", "ts": 1000, "silent_until": 1020})
        emit({"job": "checkout-2", "reason": "upstream", "ts": 1005})
        self.assertEqual(len(alert._sent), 0)
        self.assertEqual(alert.suppressions()[-1]["silent_until"], 1020)

    def test_silence_expiry_recovers_and_logs(self):
        alert.silence("upstream|checkout", seconds=20, now=1000)
        emit({"job": "checkout-1", "reason": "upstream", "ts": 1000})
        emit({"job": "checkout-2", "reason": "upstream", "ts": 1005})
        self.assertEqual(len(alert._sent), 0)

        emit({"job": "checkout-3", "reason": "upstream", "ts": 1021})
        recovery = alert.recoveries()[0]
        self.assertEqual(recovery["key"], "upstream|checkout")
        self.assertEqual(recovery["trigger"], "silence_expired")
        self.assertEqual(recovery["affected_jobs"], 2)
        self.assertEqual(recovery["silent_until"], 1020)
        expired = [r for r in alert.suppressions() if r["reason"] == "silence_expired"]
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0]["action"], "auto_recover")

        self.clock.now = 1021
        self.assertEqual(alert.tick(), len(alert.records()))

    def test_silence_does_not_expire_early(self):
        alert.silence("upstream|checkout", seconds=20, now=1000)
        emit({"job": "checkout-1", "reason": "upstream", "ts": 1000})
        self.clock.now = 1019
        alert.tick()
        self.assertEqual(len(alert.recoveries()), 0)

    def test_silence_expiry_via_tick(self):
        alert.silence("upstream|checkout", seconds=20, now=1000)
        emit({"job": "checkout-1", "reason": "upstream", "ts": 1000})
        self.clock.now = 1025
        alert.tick()
        self.assertEqual(len(alert.recoveries()), 1)
        self.assertEqual(alert.recoveries()[0]["affected_jobs"], 1)


class RecordTest(unittest.TestCase):
    def setUp(self):
        alert._sent.clear()
        alert.configure(clock=FakeClock())

    def tearDown(self):
        alert.configure(window_seconds=alert.WINDOW_SECONDS, clock=None)

    def test_records_cover_all_kinds(self):
        emit({"job": "checkout-1", "reason": "upstream", "ts": 1000})
        emit({"job": "checkout-2", "reason": "upstream", "ts": 1001})
        resolve("upstream|checkout")
        kinds = [r["type"] for r in alert.records()]
        self.assertEqual(kinds, ["aggregate", "suppression", "recovery"])


if __name__ == "__main__":
    unittest.main()
