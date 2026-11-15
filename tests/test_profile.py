import unittest

from profiler2 import observe, profile


class ProfileTest(unittest.TestCase):
    def test_count(self):
        observe([{"v": 1}, {"v": 2}])
        self.assertEqual(profile("v")["count"], 2)

    def test_mean(self):
        observe([{"w": 10}, {"w": 30}])
        self.assertEqual(profile("w")["mean"], 20.0)

    def test_fixed_threshold(self):
        observe([{"x": 1000}])
        self.assertEqual(profile("x")["outliers"], [1000.0])
