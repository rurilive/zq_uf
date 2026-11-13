import unittest

from retryall import ErrorClass, HttpError, Metrics, call


class MetricsTest(unittest.TestCase):
    def setUp(self):
        self.metrics = Metrics()

    def test_retry_counts_and_elapsed_reported(self):
        state = {"n": 0}

        def flaky():
            state["n"] += 1
            if state["n"] < 3:
                raise ValueError("boom")
            return "ok"

        call(flaky, {"base_delay": 0, "metrics": self.metrics})
        snap = self.metrics.snapshot()
        site = snap["sites"]["default"]
        self.assertEqual(snap["calls"], 1)
        self.assertEqual(snap["successes"], 1)
        self.assertEqual(site["retries"][ErrorClass.TRANSIENT], 2)
        self.assertEqual(site["attempts"][ErrorClass.TRANSIENT], 2)
        self.assertGreaterEqual(site["elapsed"], 0.0)

    def test_metrics_keyed_by_site(self):
        def boom():
            raise HttpError(500)

        with self.assertRaises(HttpError):
            call(
                boom,
                {"base_delay": 0, "metrics": self.metrics, "site": "site-a"},
            )
        with self.assertRaises(HttpError):
            call(
                boom,
                {"base_delay": 0, "metrics": self.metrics, "site": "site-b"},
            )
        snap = self.metrics.snapshot()
        self.assertEqual(snap["calls"], 2)
        self.assertEqual(snap["failures"], 2)
        self.assertIn("site-a", snap["sites"])
        self.assertIn("site-b", snap["sites"])
        self.assertEqual(
            snap["sites"]["site-a"]["attempts"][ErrorClass.SERVER], 4
        )

    def test_permanent_failure_counts_without_retries(self):
        with self.assertRaises(HttpError):
            call(
                lambda: (_ for _ in ()).throw(HttpError(404)),
                {"base_delay": 0, "metrics": self.metrics},
            )
        site = self.metrics.snapshot()["sites"]["default"]
        self.assertEqual(site["failures"], 1)
        self.assertEqual(site["attempts"][ErrorClass.PERMANENT], 1)
        self.assertNotIn(ErrorClass.PERMANENT, site["retries"])

    def test_backoff_time_accumulates(self):
        with self.assertRaises(ValueError):
            call(
                lambda: (_ for _ in ()).throw(ValueError("x")),
                {
                    "base_delay": 0.1,
                    "jitter": 0,
                    "attempts": 3,
                    "metrics": self.metrics,
                },
            )
        site = self.metrics.snapshot()["sites"]["default"]
        self.assertAlmostEqual(site["backoff"], 0.1 + 0.2)

    def test_snapshot_is_isolated_copy(self):
        call(lambda: 1, {"metrics": self.metrics})
        first = self.metrics.snapshot()
        first["sites"]["default"]["calls"] = 999
        second = self.metrics.snapshot()
        self.assertEqual(second["sites"]["default"]["calls"], 1)
