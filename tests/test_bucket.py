import unittest

from ratelimit import Limiter


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class RatelimitTest(unittest.TestCase):
    def test_first_request_does_not_sleep(self):
        clock = FakeClock()
        limiter = Limiter(interval=2.0, clock=clock, sleep=clock.sleep)
        limiter.acquire("a")
        self.assertEqual(clock.now, 0.0)

    def test_second_request_waits_between_interval(self):
        clock = FakeClock()
        limiter = Limiter(interval=2.0, clock=clock, sleep=clock.sleep)
        limiter.acquire("a")
        limiter.acquire("a")
        self.assertEqual(clock.now, 2.0)

    def test_sites_are_independent(self):
        clock = FakeClock()
        limiter = Limiter(interval=2.0, clock=clock, sleep=clock.sleep)
        limiter.acquire("a")
        limiter.acquire("b")
        self.assertEqual(clock.now, 0.0)
        self.assertEqual(set(limiter.last), {"a", "b"})

    def test_report_stored(self):
        limiter = Limiter(clock=FakeClock(), sleep=lambda seconds: None)
        limiter.report("a", 429)
        self.assertEqual(limiter.reports, [("a", 429)])
