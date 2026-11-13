import time
import unittest

from siteprobe.ratelimit import RateLimiter


class RateLimiterTest(unittest.TestCase):
    def test_limited_requests_observe_interval(self):
        limiter = RateLimiter(rate_per_sec=10)
        start = time.monotonic()
        for _ in range(4):
            limiter.acquire()
        elapsed = time.monotonic() - start
        self.assertEqual(limiter.requests, 4)
        self.assertGreaterEqual(elapsed, 0.25)

    def test_zero_rate_is_unlimited(self):
        limiter = RateLimiter(rate_per_sec=0)
        start = time.monotonic()
        for _ in range(5):
            limiter.acquire()
        self.assertLess(time.monotonic() - start, 0.05)
        self.assertEqual(limiter.requests, 5)


if __name__ == "__main__":
    unittest.main()
