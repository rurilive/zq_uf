import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import crawlclock
from crawlclock import now, sync


class FakeClock:
    """可拨动的 UTC epoch 秒时钟，让测试不依赖本机时间。"""

    def __init__(self, epoch):
        self.epoch = float(epoch)

    def __call__(self):
        return self.epoch

    def advance(self, seconds):
        self.epoch += seconds
        return self.epoch


REF_EPOCH = 1_780_000_000.0  # 2026-05-28T…Z，固定参考时刻


class StateIsolation(unittest.TestCase):
    """新用例结束即恢复默认状态，避免污染 test_clock 的原有时钟。"""

    def tearDown(self):
        crawlclock.reset_state()


class ClockTest(StateIsolation):
    """原有断言保持不变。"""

    def test_now_is_datetime(self):
        self.assertIsInstance(now(), datetime)

    def test_sync_reports_no_drift(self):
        self.assertEqual(sync(datetime.now())["drift"], 0.0)

    def test_sync_does_not_correct(self):
        self.assertFalse(sync(datetime.now())["corrected"])


class ClockFixture(StateIsolation):
    """每个用例用独立的假时钟和站点时区，互不污染。"""

    station = "America/New_York"

    def setUp(self):
        self.clock = FakeClock(REF_EPOCH)
        crawlclock.configure(
            station=self.station,
            clock=self.clock,
            drift_threshold=1.0,
            slew_rate=0.05,
            deadband=0.0,
            reset=True,
        )

    def reference(self, drift=0.0):
        """构造参考时间，使 local - reference == drift。"""
        return datetime.fromtimestamp(self.clock() - drift, timezone.utc)


class DriftCorrectionTest(ClockFixture):
    def test_reports_drift_and_plans_negative_offset(self):
        result = sync(self.reference(drift=2.0))

        self.assertAlmostEqual(result["drift"], 2.0, places=6)
        self.assertTrue(result["corrected"])
        self.assertTrue(result["reachable"])
        self.assertAlmostEqual(result["target_offset"], -2.0, places=6)
        # 平滑：刚同步时只开始朝目标走，不会一步跳到位
        self.assertEqual(result["offset"], 0.0)
        self.assertTrue(result["slewing"])

    def test_small_drift_below_threshold_is_smoothed(self):
        result = sync(self.reference(drift=0.2))

        self.assertFalse(result["alerted"])
        self.assertTrue(result["corrected"])
        self.assertAlmostEqual(result["target_offset"], -0.2, places=6)

    def test_slew_is_rate_limited_and_converges(self):
        sync(self.reference(drift=2.0))
        self.assertEqual(crawlclock.current_offset(), 0.0)

        # 校正 2 秒，速率 0.05/s => 至少需要 40 秒真实时间
        for _ in range(10):
            self.clock.advance(1.0)
            now()
        offset_after_10s = crawlclock.current_offset()
        self.assertAlmostEqual(offset_after_10s, -0.5, places=6)

        self.clock.advance(100.0)
        now()
        self.assertAlmostEqual(crawlclock.current_offset(), -2.0, places=6)
        self.assertFalse(crawlclock.status()["slewing"])

    def test_drift_over_threshold_alerts_before_correcting(self):
        result = sync(self.reference(drift=5.0))

        self.assertTrue(result["alerted"])
        self.assertTrue(result["corrected"])

        events = [r["event"] for r in crawlclock.records()]
        self.assertLess(events.index("alert"), events.index("correction"))

        alert = crawlclock.records(event="alert")[0]
        self.assertAlmostEqual(alert["drift"], 5.0, places=6)
        self.assertAlmostEqual(alert["raw_drift"], 5.0, places=6)
        self.assertEqual(alert["threshold"], 1.0)

    def test_alert_is_edge_triggered_not_repeated(self):
        # 持续超阈值期间反复同步，只记一次告警
        for _ in range(4):
            sync(self.reference(drift=3.0))
            self.clock.advance(5.0)
        self.assertEqual(len(crawlclock.records(event="alert")), 1)

        # 校正到位后解除告警
        self.clock.advance(100.0)
        result = sync(self.reference(drift=3.0))
        self.assertFalse(result["alerted"])
        self.assertEqual(len(crawlclock.records(event="alert_cleared")), 1)

    def test_converged_clock_stops_alerting(self):
        sync(self.reference(drift=2.0))
        self.clock.advance(60.0)
        sync(self.reference(drift=2.0))  # 校正完成
        self.clock.advance(10.0)

        result = sync(self.reference(drift=2.0))
        self.assertAlmostEqual(result["offset"], -2.0, places=6)
        self.assertAlmostEqual(result["drift"], 0.0, places=6)
        self.assertFalse(result["alerted"])
        self.assertFalse(result["corrected"])
        # 只有第一次同步触发过告警
        self.assertEqual(len(crawlclock.records(event="alert")), 1)

    def test_correction_never_moves_time_backwards(self):
        # 本机偏快 2 秒，校正需要把时间拉回，但 now() 必须单调
        sync(self.reference(drift=2.0))
        previous = now()
        for _ in range(200):
            self.clock.advance(0.5)
            current = now()
            self.assertGreaterEqual(current, previous)
            previous = current

    def test_sync_records_drift(self):
        sync(self.reference(drift=3.0))
        syncs = crawlclock.records(event="sync")

        self.assertEqual(len(syncs), 1)
        self.assertAlmostEqual(syncs[0]["drift"], 3.0, places=6)
        self.assertTrue(syncs[0]["reachable"])
        self.assertEqual(syncs[0]["machine"], crawlclock.machine_id())

    def test_drift_curve_is_per_machine(self):
        sync(self.reference(drift=3.0))
        self.clock.advance(10.0)  # 平滑走 0.5 秒，offset = -0.5
        sync(self.reference(drift=1.0))

        curve = crawlclock.drift_curve()
        self.assertEqual(len(curve), 2)
        # 第一次还没校正，drift == raw_drift == 3.0
        self.assertAlmostEqual(curve[0]["drift"], 3.0, places=6)
        # 第二次校正后时钟相对参考仍差 0.5 秒，原始偏差是 1.0 秒
        self.assertAlmostEqual(curve[1]["drift"], 0.5, places=6)
        self.assertAlmostEqual(curve[1]["raw_drift"], 1.0, places=6)
        self.assertTrue(all(p["machine"] == crawlclock.machine_id() for p in curve))
        self.assertLessEqual(curve[0]["epoch"], curve[1]["epoch"])

        self.assertEqual(crawlclock.drift_curve(machine="another-host"), [])

    def test_deadband_ignores_tiny_drift(self):
        crawlclock.configure(
            station=self.station, clock=self.clock, deadband=0.05, reset=True
        )
        result = sync(self.reference(drift=0.01))

        self.assertEqual(result["drift"], 0.0)
        self.assertFalse(result["corrected"])
        self.assertEqual(result["target_offset"], 0.0)


class DstTest(StateIsolation):
    def setUp(self):
        # 2026-03-08T06:30Z 是美东 01:30 EST，07:30Z 已跳到 03:30 EDT
        self.clock = FakeClock(
            datetime(2026, 3, 8, 6, 30, tzinfo=timezone.utc).timestamp()
        )
        crawlclock.configure(
            station="America/New_York", clock=self.clock, reset=True
        )

    def test_spring_forward_reanchors_and_reports_windows(self):
        before = now()
        self.assertEqual(before.utcoffset(), timedelta(hours=-5))

        self.clock.advance(2 * 3600)  # 跨过 07:00Z 的切换点
        result = sync(datetime.fromtimestamp(self.clock(), timezone.utc))

        dst = result["dst"]
        self.assertIsNotNone(dst)
        self.assertEqual(dst["direction"], "forward")
        self.assertEqual(dst["offset_before_seconds"], -5 * 3600)
        self.assertEqual(dst["offset_after_seconds"], -4 * 3600)
        self.assertEqual(dst["local_before"], "2026-03-08T01:59:59.999999-05:00")
        self.assertEqual(dst["local_after"], "2026-03-08T03:00:00-04:00")
        self.assertIn("before", dst["windows"])
        self.assertIn("after", dst["windows"])
        self.assertEqual(dst["gap"]["seconds"], 3600)

        after = now()
        self.assertEqual(after.utcoffset(), timedelta(hours=-4))
        self.assertEqual(crawlclock.station_name(), "America/New_York")

    def test_dst_switch_is_recorded_once(self):
        self.clock.advance(2 * 3600)
        sync(datetime.fromtimestamp(self.clock(), timezone.utc))

        tz_records = crawlclock.records(event="timezone")
        self.assertEqual(len(tz_records), 1)
        self.assertEqual(tz_records[0]["direction"], "forward")
        # 前后窗口各以切换点为中心、长度等于偏移变化量（1 小时）
        self.assertEqual(tz_records[0]["windows"]["before"]["seconds"], 3600)
        self.assertEqual(tz_records[0]["windows"]["after"]["seconds"], 3600)
        self.assertEqual(
            tz_records[0]["windows"]["before"]["start_utc"], "2026-03-08T06:00:00+00:00"
        )
        self.assertEqual(
            tz_records[0]["windows"]["after"]["end_utc"], "2026-03-08T08:00:00+00:00"
        )

        # 同一天后续同步不应重复写切换记录
        self.clock.advance(60)
        sync(datetime.fromtimestamp(self.clock(), timezone.utc))
        self.assertEqual(len(crawlclock.records(event="timezone")), 1)

    def test_fall_back_gap_window(self):
        self.clock.epoch = datetime(
            2026, 11, 1, 5, 30, tzinfo=timezone.utc
        ).timestamp()
        crawlclock.configure(station="America/New_York", clock=self.clock, reset=True)
        before = now()
        self.assertEqual(before.utcoffset(), timedelta(hours=-4))

        self.clock.advance(2 * 3600)  # 06:00Z 回拨到 01:00 EST
        result = sync(datetime.fromtimestamp(self.clock(), timezone.utc))

        dst = result["dst"]
        self.assertEqual(dst["direction"], "backward")
        self.assertEqual(dst["offset_before_seconds"], -4 * 3600)
        self.assertEqual(dst["offset_after_seconds"], -5 * 3600)
        self.assertEqual(dst["local_before"], "2026-11-01T01:59:59.999999-04:00")
        self.assertEqual(dst["local_after"], "2026-11-01T01:00:00-05:00")
        self.assertEqual(dst["overlap"]["seconds"], 3600)
        self.assertNotIn("gap", dst)

    def test_offline_gap_across_two_transitions_reports_both(self):
        # 停机两个月，期间先春后秋，端点偏移恰好相同也不能漏记
        self.clock.epoch = datetime(
            2026, 1, 1, tzinfo=timezone.utc
        ).timestamp()
        crawlclock.configure(station="America/New_York", clock=self.clock, reset=True)
        self.assertEqual(now().utcoffset(), timedelta(hours=-5))

        self.clock.epoch = datetime(
            2026, 12, 1, tzinfo=timezone.utc
        ).timestamp()
        result = sync(datetime.fromtimestamp(self.clock(), timezone.utc))

        dst = result["dst"]
        self.assertIsNotNone(dst)
        transitions = dst["transitions"]
        self.assertEqual(
            [t["direction"] for t in transitions], ["forward", "backward"]
        )
        self.assertEqual(dst["transition_utc"], "2026-11-01T06:00:00+00:00")
        self.assertEqual(dst["direction"], "backward")
        self.assertEqual(now().utcoffset(), timedelta(hours=-5))
        self.assertEqual(len(crawlclock.records(event="timezone")), 1)

    def test_half_hour_dst_zone(self):
        # Australia/Lord_Howe 的 DST 只跳 30 分钟，验证不是硬编码 1 小时
        self.clock.epoch = datetime(
            2026, 10, 3, 14, 30, tzinfo=timezone.utc
        ).timestamp()
        crawlclock.configure(
            station="Australia/Lord_Howe", clock=self.clock, reset=True
        )
        self.assertEqual(now().utcoffset(), timedelta(hours=10, minutes=30))

        self.clock.advance(2 * 3600)
        result = sync(datetime.fromtimestamp(self.clock(), timezone.utc))

        dst = result["dst"]
        self.assertEqual(dst["direction"], "forward")
        self.assertEqual(dst["offset_after_seconds"] - dst["offset_before_seconds"], 1800)
        self.assertEqual(dst["gap"]["seconds"], 1800)
        self.assertEqual(dst["windows"]["before"]["seconds"], 1800)
        self.assertEqual(dst["local_before"], "2026-10-04T01:59:59.999999+10:30")
        self.assertEqual(dst["local_after"], "2026-10-04T02:30:00+11:00")
        self.assertEqual(now().utcoffset(), timedelta(hours=11))

    def test_site_timezone_used_for_cross_zone_window(self):
        # 上海时间 2026-03-09 09:00 对应美东 2026-03-08 21:00（已切 DST）
        crawlclock.configure(
            station="America/New_York", clock=self.clock, reset=True
        )
        window = crawlclock.station_window(
            "2026-03-09T09:00:00+08:00", "2026-03-09T10:30:00+08:00"
        )
        self.assertEqual(window["timezone"], "America/New_York")
        self.assertEqual(window["start"].hour, 21)
        self.assertEqual(window["end"].hour, 22)
        self.assertEqual(window["end"].minute, 30)
        self.assertEqual(window["start"].utcoffset(), timedelta(hours=-4))
        self.assertEqual(window["seconds"], 5400)


class UnreachableReferenceTest(ClockFixture):
    def test_unreachable_keeps_last_offset(self):
        sync(self.reference(drift=2.0))
        self.clock.advance(20.0)
        now()
        offset_before = crawlclock.current_offset()
        self.assertAlmostEqual(offset_before, -1.0, places=6)

        result = sync(None)  # 参考时间不可达

        self.assertFalse(result["reachable"])
        self.assertFalse(result["corrected"])
        self.assertAlmostEqual(result["offset"], offset_before, places=6)
        self.assertAlmostEqual(crawlclock.current_offset(), offset_before, places=6)
        # 漂移沿用上次值，而不是伪造为 0
        self.assertAlmostEqual(result["drift"], 2.0, places=6)
        self.assertAlmostEqual(result["raw_drift"], 2.0, places=6)

        unreachable = crawlclock.records(event="unreachable")
        self.assertEqual(len(unreachable), 1)
        self.assertEqual(unreachable[0]["reason"], "ReferenceUnavailable")

    def test_raising_source_is_treated_as_unreachable(self):
        def broken():
            raise TimeoutError("ntp timeout")

        result = sync(broken)
        self.assertFalse(result["reachable"])
        self.assertEqual(
            crawlclock.records(event="unreachable")[0]["reason"], "TimeoutError"
        )

    def test_garbage_reference_is_unreachable(self):
        result = sync("not-a-time")
        self.assertFalse(result["reachable"])
        self.assertTrue(crawlclock.records(event="unreachable"))


class RecordFileTest(ClockFixture):
    def test_records_written_to_jsonl_and_reloadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clock" / "drift.jsonl"
            crawlclock.configure(
                station=self.station,
                clock=self.clock,
                log_path=path,
                drift_threshold=1.0,
                reset=True,
            )
            sync(self.reference(drift=2.5))

            loaded = crawlclock.load_records(path)
            events = [r["event"] for r in loaded]
            self.assertIn("sync", events)
            self.assertIn("alert", events)
            self.assertIn("correction", events)

            sync_record = next(r for r in loaded if r["event"] == "sync")
            self.assertAlmostEqual(sync_record["drift"], 2.5, places=6)

            curve = crawlclock.drift_curve(records=loaded)
            self.assertEqual(len(curve), 1)
            self.assertAlmostEqual(curve[0]["drift"], 2.5, places=6)

    def test_outputs_include_drift_correction_and_timezone(self):
        result = sync(self.reference(drift=2.0))
        for key in ("drift", "corrected", "timezone", "offset", "target_offset"):
            self.assertIn(key, result)
        self.assertEqual(result["timezone"], "America/New_York")

        snapshot = crawlclock.status()
        self.assertEqual(snapshot["timezone"], "America/New_York")
        self.assertIn("last_drift", snapshot)


class ConfigTest(StateIsolation):
    def test_rejects_slew_rate_that_could_move_time_backwards(self):
        with self.assertRaises(ValueError):
            crawlclock.configure(slew_rate=1.5, reset=True)
        with self.assertRaises(ValueError):
            crawlclock.configure(slew_rate=0, reset=True)

    def test_rejects_unknown_station(self):
        with self.assertRaises(ValueError):
            crawlclock.configure(station="Mars/Olympus_Mons", reset=True)

    def test_naive_reference_interpreted_in_station_zone(self):
        clock = FakeClock(datetime(2026, 6, 1, 12, tzinfo=timezone.utc).timestamp())
        crawlclock.configure(station="Asia/Shanghai", clock=clock, reset=True)
        # 站点时区下 20:00，本机也是 20:00 => 漂移 0
        result = sync(datetime(2026, 6, 1, 20, 0))
        self.assertAlmostEqual(result["drift"], 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
