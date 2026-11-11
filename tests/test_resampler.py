import unittest

from audioresample import Resampler


class ResamplerTest(unittest.TestCase):
    def test_same_rate(self):
        resampler = Resampler(48000, 48000)
        resampler.push([0, 10, 20, 30, 40], 0)
        self.assertEqual(resampler.pull(3), [0, 10, 20])

    def test_upsample_count(self):
        resampler = Resampler(24000, 48000)
        resampler.push(list(range(100)), 0)
        self.assertGreater(len(resampler.pull(20)), 0)

    def test_downsample_count(self):
        resampler = Resampler(48000, 24000)
        resampler.push(list(range(100)), 0)
        self.assertGreater(len(resampler.pull(10)), 0)

    def test_snapshot_restore(self):
        resampler = Resampler(48000, 48000)
        resampler.push([1, 2, 3, 4], 0)
        state = resampler.snapshot()
        resampler.pull(1)
        resampler.restore(state)
        self.assertEqual(resampler.pull(1), [1])
