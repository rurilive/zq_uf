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


class OutOfOrderTest(unittest.TestCase):
    def test_close_preserves_arrival_order(self):
        window = Window(size=5)
        window.push({"ts": 4})
        window.push({"ts": 2})
        window.push({"ts": 7})
        self.assertEqual(window.advance(10), [{"ts": 4}, {"ts": 2}])
        self.assertEqual(window.buffer, [{"ts": 7}])

    def test_quantile_watermark(self):
        window = Window(size=5, quantile=0.5)
        for ts in (10, 30, 20):
            window.push({"ts": ts})
        self.assertEqual(window.watermark, 20)

    def test_per_source_lateness(self):
        window = Window(size=5, allowed_lateness={"a": 5, "default": 0})
        window.push({"ts": 1, "source": "a"})
        window.push({"ts": 2})
        self.assertEqual(window.advance(10), [{"ts": 2}])
        self.assertEqual(window.advance(11), [{"ts": 1, "source": "a"}])

    def test_idle_advance_by_processing_time(self):
        window = Window(size=5, idle_timeout=10)
        window.push({"ts": 1})
        self.assertEqual(window.advance(5), [])
        self.assertEqual(window.buffer, [{"ts": 1}])
        self.assertEqual(window.advance(20), [{"ts": 1}])

    def test_side_output_records_late_event(self):
        window = Window(size=5)
        window.push({"ts": 1})
        window.advance(10)
        window.push({"ts": 2})
        self.assertEqual(window.buffer, [])
        self.assertEqual(len(window.side_output), 1)
        record = window.side_output[0]
        self.assertEqual(record["event"], {"ts": 2})
        self.assertEqual(record["window"], (0, 5))
        self.assertEqual(record["lateness"], 5)
