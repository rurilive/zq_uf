import unittest

from proxycheck import check, pick
from proxycheck.health import EJECTED, HEALTHY, HealthMonitor


class ProxycheckTest(unittest.TestCase):
    def test_check_records_probe(self):
        results = check(["p1", "p2"], lambda proxy: (proxy == "p1", 10))
        self.assertEqual(results[0], {"proxy": "p1", "ok": True, "latency": 10})
        self.assertFalse(results[1]["ok"])

    def test_pick_first_healthy(self):
        results = check(["p1", "p2", "p3"], lambda proxy: (proxy != "p1", 5))
        self.assertEqual(pick(results), "p2")

    def test_pick_none_when_all_bad(self):
        results = check(["p1"], lambda proxy: (False, 99))
        self.assertIsNone(pick(results))


class FakeClock:
    """可手动推进的时钟，避免真实 sleep 也保证冷却逻辑确定。"""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class EjectionTest(unittest.TestCase):
    def test_consecutive_failures_eject_proxy(self):
        clock = FakeClock()
        monitor = HealthMonitor(
            lambda proxy: (False, None, "connection refused"),
            attempts=3,
            fail_threshold=3,
            cooldown=10.0,
            clock=clock,
        )

        results = monitor.check(["p1", "p2"])
        self.assertEqual(results[0]["proxy"], "p1")
        self.assertFalse(results[0]["ok"])

        state = monitor.get_state("p1")
        self.assertEqual(state.status, EJECTED)
        self.assertEqual(state.consecutive_failures, 3)
        self.assertEqual(state.success_rate, 0.0)
        self.assertEqual(state.last_error, "connection refused")
        self.assertEqual(state.ejected_at, clock())

    def test_ejected_proxy_skipped_during_cooldown(self):
        clock = FakeClock()
        calls = []

        def probe(proxy):
            calls.append(proxy)
            return False, None, "boom"

        monitor = HealthMonitor(
            probe, attempts=1, fail_threshold=1, cooldown=30.0, clock=clock
        )
        monitor.check(["p1"])
        self.assertEqual(monitor.get_state("p1").status, EJECTED)

        calls.clear()
        results = monitor.check(["p1"])
        self.assertEqual(calls, [])  # 冷却中不再探测
        self.assertFalse(results[0]["ok"])
        self.assertEqual(monitor.get_state("p1").status, EJECTED)

    def test_partial_failure_does_not_eject(self):
        clock = FakeClock()
        pattern = iter([True, False, False])
        monitor = HealthMonitor(
            lambda proxy: (next(pattern), 5),
            attempts=3,
            fail_threshold=3,
            clock=clock,
        )
        monitor.check(["p1"])
        state = monitor.get_state("p1")
        self.assertEqual(state.status, HEALTHY)
        self.assertEqual(state.consecutive_failures, 2)
        self.assertAlmostEqual(state.success_rate, 1 / 3)


class RecoveryTest(unittest.TestCase):
    def test_trial_recovers_after_cooldown(self):
        clock = FakeClock()
        answers = iter([(False, None, "down"), (True, 12, None)])
        monitor = HealthMonitor(
            lambda proxy: next(answers),
            attempts=1,
            fail_threshold=1,
            cooldown=30.0,
            recovery_threshold=1.0,
            clock=clock,
        )

        monitor.check(["p1"])
        self.assertEqual(monitor.get_state("p1").status, EJECTED)

        clock.advance(31.0)
        results = monitor.check(["p1"])
        state = monitor.get_state("p1")
        self.assertEqual(state.status, HEALTHY)
        self.assertTrue(results[0]["ok"])
        self.assertEqual(state.consecutive_failures, 0)
        self.assertIsNone(state.ejected_at)
        self.assertEqual(monitor.pick(results), "p1")

    def test_failed_trial_goes_back_to_cooldown(self):
        clock = FakeClock()
        answers = iter([(False, None, "down"), (False, None, "still down")])
        monitor = HealthMonitor(
            lambda proxy: next(answers),
            attempts=1,
            fail_threshold=1,
            cooldown=30.0,
            clock=clock,
        )

        monitor.check(["p1"])
        first_ejected_at = monitor.get_state("p1").ejected_at
        clock.advance(31.0)
        results = monitor.check(["p1"])

        state = monitor.get_state("p1")
        self.assertEqual(state.status, EJECTED)
        self.assertEqual(state.last_error, "still down")
        self.assertGreater(state.ejected_at, first_ejected_at)
        self.assertFalse(results[0]["ok"])

    def test_recovery_threshold_requires_full_success(self):
        clock = FakeClock()
        answers = iter([(False, None, "x"), True, False])

        def probe(proxy):
            answer = next(answers)
            if isinstance(answer, tuple):
                return answer
            return answer, 5, None

        monitor = HealthMonitor(
            probe,
            attempts=2,
            fail_threshold=1,
            cooldown=5.0,
            recovery_threshold=1.0,
            clock=clock,
        )
        monitor.check(["p1"])
        self.assertEqual(monitor.get_state("p1").status, EJECTED)

        clock.advance(6.0)
        monitor.check(["p1"])
        self.assertEqual(monitor.get_state("p1").status, EJECTED)  # 试探轮仅 50% 成功

        clock.advance(6.0)
        answers2 = iter([(True, 5, None), (True, 6, None)])
        monitor.check(["p1"], probe=lambda proxy: next(answers2))
        self.assertEqual(monitor.get_state("p1").status, HEALTHY)


class ConcurrencyTest(unittest.TestCase):
    def test_probes_run_concurrently(self):
        import time as real_time

        seen = []
        lock = __import__("threading").Lock()

        def probe(proxy):
            with lock:
                seen.append(proxy)
            real_time.sleep(0.05)
            return True, 10, None

        monitor = HealthMonitor(
            probe, attempts=3, workers=12, round_timeout=5.0, clock=real_time.monotonic
        )
        start = real_time.monotonic()
        results = monitor.check(["p1", "p2", "p3", "p4"])
        elapsed = real_time.monotonic() - start

        self.assertEqual(len(seen), 12)
        self.assertTrue(all(item["ok"] for item in results))
        self.assertLess(elapsed, 0.4)  # 串行至少 0.6s，并发应远低于此

    def test_round_timeout_marks_pending_as_failure(self):
        import time as real_time

        def probe(proxy):
            real_time.sleep(0.3)
            return True, 10, None

        monitor = HealthMonitor(
            probe,
            attempts=1,
            fail_threshold=1,
            workers=1,
            round_timeout=0.1,
            clock=real_time.monotonic,
        )
        results = monitor.check(["p1"])
        self.assertFalse(results[0]["ok"])
        self.assertIn("timeout", monitor.get_state("p1").last_error)
        self.assertEqual(monitor.get_state("p1").status, EJECTED)

    def test_probe_exception_counts_as_failure(self):
        clock = FakeClock()

        def probe(proxy):
            raise RuntimeError("network exploded")

        monitor = HealthMonitor(
            probe, attempts=1, fail_threshold=1, clock=clock
        )
        monitor.check(["p1"])
        state = monitor.get_state("p1")
        self.assertEqual(state.status, EJECTED)
        self.assertIn("network exploded", state.last_error)


class ScoringTest(unittest.TestCase):
    def test_pick_orders_by_success_rate_then_latency(self):
        clock = FakeClock()
        counters = {"flaky": 0}

        def probe(proxy):
            if proxy == "fast":
                return True, 10, None
            if proxy == "slow":
                return True, 300, None
            counters[proxy] += 1
            return counters[proxy] % 2 == 0, 50, None  # 一半成功

        monitor = HealthMonitor(
            probe, attempts=4, fail_threshold=99, clock=clock
        )
        results = monitor.check(["flaky", "slow", "fast"])

        self.assertEqual(monitor.pick(results), "fast")
        ranking = [item["proxy"] for item in monitor.snapshot()]
        self.assertEqual(ranking, ["fast", "slow", "flaky"])

        fast = monitor.get_state("fast")
        flaky = monitor.get_state("flaky")
        self.assertGreater(monitor.score(fast), monitor.score(flaky))
        self.assertAlmostEqual(flaky.success_rate, 0.5)
        self.assertEqual(fast.status, HEALTHY)

    def test_snapshot_exposes_observable_fields(self):
        clock = FakeClock()
        monitor = HealthMonitor(
            lambda proxy: (False, None, "refused"),
            attempts=2,
            fail_threshold=2,
            clock=clock,
        )
        monitor.check(["p1"])
        item = monitor.snapshot()[0]
        self.assertEqual(
            set(item),
            {
                "proxy",
                "status",
                "success_rate",
                "latency",
                "last_error",
                "consecutive_failures",
                "attempts",
                "successes",
                "total_attempts",
                "total_successes",
            },
        )
        self.assertEqual(item["status"], EJECTED)
        self.assertEqual(item["last_error"], "refused")
        self.assertIn("p1", monitor.report())
