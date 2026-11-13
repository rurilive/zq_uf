import unittest
from datetime import datetime, timezone

from downsample import resample, rollup


class DownsampleTest(unittest.TestCase):
    def test_takes_every_step(self):
        points = [{"ts": i, "v": i} for i in range(6)]
        self.assertEqual([p["ts"] for p in resample(points, 2)], [0, 2, 4])

    def test_step_one_keeps_all(self):
        points = [{"ts": 1}, {"ts": 2}]
        self.assertEqual(len(resample(points, 1)), 2)

    def test_empty(self):
        self.assertEqual(resample([], 3), [])


class WindowedResampleTest(unittest.TestCase):
    def test_peak_preserved_in_window(self):
        points = [{"ts": 0, "v": 1}, {"ts": 3, "v": 100}, {"ts": 7, "v": 2}]
        [window] = resample(points, "10s")
        self.assertEqual(window["max"], 100)
        self.assertEqual(window["min"], 1)
        self.assertEqual(window["last"], 2)
        self.assertEqual(window["count"], 3)
        self.assertAlmostEqual(window["mean"], 103 / 3)

    def test_aggs_configurable(self):
        points = [{"ts": 0, "v": 1}, {"ts": 1, "v": 3}]
        [window] = resample(points, "10s", aggs=["max"])
        self.assertEqual(window["max"], 3)
        self.assertNotIn("min", window)
        self.assertNotIn("mean", window)

    def test_fixed_window_aligned_to_epoch(self):
        [window] = resample([{"ts": 601, "v": 1}], "10m")
        self.assertEqual((window["start"], window["end"]), (600, 1200))

    def test_calendar_day_aligned_to_utc_midnight(self):
        ts = datetime(2024, 3, 5, 13, 20, tzinfo=timezone.utc).timestamp()
        [window] = resample([{"ts": ts, "v": 1}], "1day")
        self.assertEqual(
            window["start"],
            int(datetime(2024, 3, 5, tzinfo=timezone.utc).timestamp()),
        )
        self.assertEqual(window["end"] - window["start"], 86400)

    def test_calendar_month_window(self):
        ts = datetime(2024, 3, 15, tzinfo=timezone.utc).timestamp()
        [window] = resample([{"ts": ts, "v": 1}], "1month")
        self.assertEqual(
            window["start"],
            int(datetime(2024, 3, 1, tzinfo=timezone.utc).timestamp()),
        )
        self.assertEqual(
            window["end"],
            int(datetime(2024, 4, 1, tzinfo=timezone.utc).timestamp()),
        )

    def test_out_of_order_points_land_in_their_windows(self):
        points = [{"ts": 900, "v": 3}, {"ts": 5, "v": 1}, {"ts": 610, "v": 2}]
        windows = resample(points, "10m")
        self.assertEqual([w["start"] for w in windows], [0, 600])
        self.assertEqual(windows[0]["last"], 1)
        self.assertEqual(windows[1]["count"], 2)
        self.assertEqual(windows[1]["max"], 3)

    def test_duplicate_timestamp_overwrite(self):
        points = [{"ts": 1, "v": 1}, {"ts": 1, "v": 5}]
        [window] = resample(points, {"every": "10s", "duplicate": "overwrite"})
        self.assertEqual((window["count"], window["last"]), (1, 5))

    def test_duplicate_timestamp_accumulate(self):
        points = [{"ts": 1, "v": 1}, {"ts": 1, "v": 5}]
        [window] = resample(points, {"every": "10s", "duplicate": "accumulate"})
        self.assertEqual((window["count"], window["max"]), (1, 6))

    def test_empty_windows_skipped_by_default(self):
        points = [{"ts": 0, "v": 1}, {"ts": 2400, "v": 2}]
        windows = resample(points, "10m")
        self.assertEqual([w["start"] for w in windows], [0, 2400])

    def test_empty_windows_padded_with_gap_stats(self):
        points = [{"ts": 0, "v": 1}, {"ts": 2400, "v": 2}]
        windows = resample(points, {"every": "10m", "fill": "pad"})
        self.assertEqual([w["start"] for w in windows], [0, 600, 1200, 1800, 2400])
        gaps = [w for w in windows if w["gap"]]
        self.assertEqual(len(gaps), 3)
        for gap in gaps:
            self.assertEqual(gap["count"], 0)
            self.assertIsNone(gap["max"])
            self.assertEqual(gap["gap_len"], 3)
        self.assertEqual([w["gap_len"] for w in windows if not w["gap"]], [0, 0])

    def test_multi_level_rollup_matches_direct(self):
        points = (
            [{"ts": 60 * i, "v": float(i % 7)} for i in range(30)]
            + [{"ts": 3600 + 60 * i, "v": float(100 + i)} for i in range(10)]
        )
        direct = resample(points, "1h")
        staged = rollup(resample(points, "10m"), "1h")
        self.assertEqual(len(direct), len(staged))
        for expected, actual in zip(direct, staged):
            self.assertEqual(expected["start"], actual["start"])
            self.assertEqual(expected["end"], actual["end"])
            self.assertEqual(expected["count"], actual["count"])
            self.assertEqual(expected["min"], actual["min"])
            self.assertEqual(expected["max"], actual["max"])
            self.assertEqual(expected["last"], actual["last"])
            self.assertAlmostEqual(expected["mean"], actual["mean"], places=9)
