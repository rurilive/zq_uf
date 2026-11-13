import unittest

from downsample import resample


class DownsampleTest(unittest.TestCase):
    def test_takes_every_step(self):
        points = [{"ts": i, "v": i} for i in range(6)]
        self.assertEqual([p["ts"] for p in resample(points, 2)], [0, 2, 4])

    def test_step_one_keeps_all(self):
        points = [{"ts": 1}, {"ts": 2}]
        self.assertEqual(len(resample(points, 1)), 2)

    def test_empty(self):
        self.assertEqual(resample([], 3), [])
