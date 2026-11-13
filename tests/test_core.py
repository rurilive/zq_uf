import unittest

from watermark import Window


class WatermarkTest(unittest.TestCase):
    def test_watermark_tracks_max(self):
        window = Window(size=5)
        window.push({"ts": 3})
        window.push({"ts": 8})
        self.assertEqual(window.watermark, 8)

    def test_advance_closes_old(self):
        window = Window(size=5)
        window.push({"ts": 1})
        self.assertEqual(window.advance(10), [{"ts": 1}])

    def test_future_events_stay(self):
        window = Window(size=5)
        window.push({"ts": 9})
        self.assertEqual(window.advance(10), [])
        self.assertEqual(window.buffer, [{"ts": 9}])
