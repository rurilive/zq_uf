import unittest
from unittest import mock

from retryall import ErrorClass, HttpError, Policy, call, compute_delay


class BackoffTest(unittest.TestCase):
    def test_exponential_growth_without_jitter(self):
        policy = Policy({"jitter": 0})
        delays = [
            compute_delay(policy, ErrorClass.TRANSIENT, index)
            for index in range(3)
        ]
        self.assertEqual(delays, [0.01, 0.02, 0.04])

    def test_jitter_stays_inside_band(self):
        policy = Policy({"base_delay": 1.0, "jitter": 0.25})
        for _ in range(50):
            delay = compute_delay(policy, ErrorClass.TRANSIENT, 0)
            self.assertGreaterEqual(delay, 0.75)
            self.assertLessEqual(delay, 1.25)

    def test_jitter_uses_uniform_around_zero(self):
        policy = Policy({"base_delay": 1.0, "jitter": 0.5})
        with mock.patch("retryall.core.random.uniform", return_value=0.4) as rng:
            delay = compute_delay(policy, ErrorClass.TRANSIENT, 0)
        self.assertAlmostEqual(delay, 1.2)
        rng.assert_called_once_with(-1.0, 1.0)

    def test_max_delay_cap_per_error_class(self):
        policy = Policy(
            {
                "base_delay": 0.5,
                "jitter": 0,
                "classes": {
                    ErrorClass.TIMEOUT: {"max_delay": 0.5},
                }
            }
        )
        for index in range(6):
            delay = compute_delay(policy, ErrorClass.TIMEOUT, index)
            self.assertLessEqual(delay, 0.5)
        self.assertEqual(
            compute_delay(policy, ErrorClass.TIMEOUT, 5), 0.5
        )

    def test_delay_never_negative_or_over_cap(self):
        policy = Policy({"base_delay": 1.0, "jitter": 1.0})
        config = policy.class_config(ErrorClass.TRANSIENT)
        for _ in range(50):
            delay = compute_delay(policy, ErrorClass.TRANSIENT, 0)
            self.assertGreaterEqual(delay, 0.0)
            self.assertLessEqual(delay, config["max_delay"])

    def test_call_sleeps_computed_jittered_delay(self):
        policy = {"base_delay": 0.1, "jitter": 0.5, "attempts": 2}
        with mock.patch("retryall.core.random.uniform", return_value=-1.0):
            with mock.patch("retryall.core.time.sleep") as sleep:
                with self.assertRaises(ValueError):
                    call(lambda: (_ for _ in ()).throw(ValueError("x")), policy)
        sleep.assert_called_once_with(0.05)

    def test_retry_after_raises_floor_but_respects_cap(self):
        policy = Policy(
            {
                "classes": {
                    ErrorClass.RATE_LIMIT: {"max_delay": 2.0},
                },
                "jitter": 0,
            }
        )
        delay = compute_delay(
            policy, ErrorClass.RATE_LIMIT, 0, HttpError(429, retry_after=1.5)
        )
        self.assertEqual(delay, 1.5)
        delay = compute_delay(
            policy, ErrorClass.RATE_LIMIT, 0, HttpError(429, retry_after=99)
        )
        self.assertEqual(delay, 2.0)
