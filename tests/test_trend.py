import unittest

from qtrend2 import (
    Config,
    Engine,
    check,
    reset,
    sweep_thresholds,
    trend,
    mark_false_positive,
    backtest,
    label_shadow,
    shadow_report,
)


class TrendTest(unittest.TestCase):
    def setUp(self):
        reset()

    # ---- 原有接口行为保持兼容 ---- #
    def test_fixed_threshold_alerts(self):
        self.assertTrue(check("d1", {"value": 0.5})["alert"])

    def test_low_value_quiet(self):
        self.assertFalse(check("d2", {"value": 0.01})["alert"])

    def test_no_direction(self):
        self.assertIsNone(trend("d3")["direction"])

    # ---- 基线随业务量平移，不再误报 ---- #
    def test_baseline_tracks_level_shift(self):
        eng = Engine(Config(min_samples=5, cold_start_threshold=None))
        # 业务量整体上了一个台阶（如 0.02 -> 0.08），新值在新基线上正常波动
        for v in [0.078, 0.081, 0.079, 0.082, 0.08, 0.077, 0.081, 0.08]:
            r = eng.check("orders", "error_rate", v)
            self.assertFalse(r["alert"], f"业务平移后的正常波动不应告警: {v}")
        self.assertIsNotNone(r["baseline"])
        self.assertAlmostEqual(r["baseline"]["median"], 0.08, delta=0.005)

    def test_sudden_drop_alerts_with_context(self):
        eng = Engine(Config(min_samples=5, window=20, season=None))
        history = [0.10, 0.101, 0.099, 0.10, 0.102, 0.098, 0.1, 0.101]
        for v in history:
            eng.check("orders", "error_rate", v)
        r = eng.check("orders", "error_rate", 0.01)  # 断崖式下降
        self.assertTrue(r["alert"])
        self.assertEqual(r["reason"], "deviation")
        # 告警带基线、偏离幅度、样本、历史片段
        self.assertIn("baseline", r)
        self.assertGreater(r["relative_change"], 0.5)
        self.assertGreaterEqual(len(r["samples"]), 5)
        self.assertEqual(r["history"][-1], 0.01)
        self.assertIn(0.01, r["history"])

    # ---- 缓慢下降需要连续多个周期确认 ---- #
    def test_slow_decline_needs_consecutive_confirmation(self):
        eng = Engine(Config(min_samples=5, window=20, season=None,
                            drift_consecutive=3, min_relative_change=0.1,
                            cold_start_threshold=None))
        for v in [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]:
            self.assertFalse(eng.check("ds", "m", v)["alert"])
        # 每期缓慢下降，单期偏离不大（强 z 不一定触发），连续三期确认
        r1 = eng.check("ds", "m", 0.085)
        r2 = eng.check("ds", "m", 0.072)
        r3 = eng.check("ds", "m", 0.061)
        self.assertFalse(r1["alert"])
        self.assertFalse(r2["alert"])
        self.assertTrue(r3["alert"])
        self.assertEqual(r3["reason"], "consecutive_drift")
        self.assertEqual(r3["consecutive_breaches"], 3)

    def test_single_dip_recovers_without_alert(self):
        eng = Engine(Config(min_samples=5, window=20, season=None))
        for v in [0.1, 0.1, 0.1, 0.1, 0.1, 0.1]:
            eng.check("ds", "m", v)
        self.assertFalse(eng.check("ds", "m", 0.085)["alert"])  # 一期小降
        r = eng.check("ds", "m", 0.1)                            # 恢复，计数清零
        self.assertFalse(r["alert"])
        self.assertEqual(r["consecutive_breaches"], 0)

    # ---- 同周期（季节性）取样本 ---- #
    def test_seasonal_same_phase_samples(self):
        eng = Engine(Config(season=7, window=3, min_samples=4))
        # 21 天周期数据，周末（period % 7 in 5,6）天然偏高
        for day in range(21):
            base = 0.2 if day % 7 in (5, 6) else 0.1
            eng.check("traffic", "m", base, period=day % 7)
        r = eng.check("traffic", "m", 0.205, period=6)  # 又一个周末
        samples = r["samples"]
        # 同时包含最近窗口和同周期（season=7 相位 6）的历史点
        self.assertIn(0.2, samples)
        self.assertGreaterEqual(len(samples), 4)
        self.assertFalse(r["alert"])  # 周末高值是基线的一部分，不误报

    # ---- 趋势按 dataset × metric 分开 ---- #
    def test_trend_per_dataset_and_metric(self):
        eng = Engine(Config(season=None))
        for v in [0.1, 0.09, 0.08, 0.07]:
            eng.check("a", "m1", v)
        for v in [0.1, 0.11, 0.12, 0.13]:
            eng.check("a", "m2", v)
        eng.check("b", "m1", 0.5)
        self.assertEqual(eng.trend("a", "m1")["direction"], "down")
        self.assertEqual(eng.trend("a", "m2")["direction"], "up")
        self.assertIsNone(eng.trend("b", "m1")["direction"])  # 单点无方向
        self.assertIsNone(eng.trend("missing", "x")["direction"])

    # ---- 误报标记后不重复提醒，恢复后重新武装 ---- #
    def test_false_positive_suppression_and_rearm(self):
        eng = Engine(Config(min_samples=5, window=20, season=None))
        for v in [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]:
            eng.check("ds", "m", v)
        first = eng.check("ds", "m", 0.02)
        self.assertTrue(first["alert"])
        mark = mark_false_positive  # import sanity; 引擎方法才是实际路径
        self.assertTrue(callable(mark))
        eng.mark_false_positive("ds", "m", alert_id=first["alert_id"], note="业务变更")
        # 同样的告警再次出现 -> 抑制，不重复提醒
        again = eng.check("ds", "m", 0.02)
        self.assertFalse(again["alert"])
        self.assertTrue(again["suppressed"])
        self.assertEqual(again["reason"], "suppressed_false_positive")
        # 恢复到基线水平 -> 自动重新武装
        eng.check("ds", "m", 0.1)
        re_alert = eng.check("ds", "m", 0.02)
        self.assertTrue(re_alert["alert"])
        self.assertFalse(re_alert["suppressed"])

    # ---- 回测：同一份数据，新旧规则对比 ---- #
    def test_backtest_same_data_compares_rules(self):
        # 业务量台阶上移（label=0 正常），末尾一次真实故障（label=1）
        points = [{"dataset": "d", "value": v, "label": 0}
                  for v in [0.08, 0.081, 0.079, 0.082, 0.08, 0.078, 0.081, 0.08]]
        points.append({"dataset": "d", "value": 0.005, "label": 1})
        eng = Engine(Config(min_samples=5, window=20, season=None,
                            cold_start_threshold=None))
        report = eng.backtest(points)
        self.assertEqual(report["points"], len(points))
        # 旧固定阈值把 0.08 平台全部误报（>0.05）
        self.assertGreater(report["legacy_threshold"]["alerts"],
                           report["new_rule"]["alerts"])
        self.assertEqual(report["new_rule"]["true_positives"], 1)
        self.assertEqual(report["new_rule"]["false_positives"], 0)
        self.assertGreater(report["legacy_threshold"]["false_positives"], 0)
        # 回测不污染引擎自身状态
        self.assertEqual(eng.trend("d")["history"], [])

    def test_sweep_thresholds(self):
        points = [{"dataset": "d", "value": v, "label": 0}
                  for v in [0.1, 0.1, 0.1, 0.1, 0.1, 0.1]]
        points += [
            {"dataset": "d", "value": 0.08, "label": 0},
            {"dataset": "d", "value": 0.07, "label": 0},
            {"dataset": "d", "value": 0.05, "label": 1},
        ]
        eng = Engine(Config(season=None))
        grid = sweep_thresholds(points, deviation_zs=(2.0, 3.0),
                                drift_consecutives=(2, 3))
        self.assertEqual(len(grid), 4)
        self.assertTrue(all("false_positive_rate" in row for row in grid))

    # ---- 影子运行 + 人工标签对比 ---- #
    def test_shadow_run_never_alerts_and_reports(self):
        eng = Engine(Config(min_samples=5, window=20, season=None))
        shadow_ids = []
        for v in [0.08, 0.081, 0.079, 0.082, 0.08, 0.078]:
            r = eng.check("d", "m", v, shadow=True)
            self.assertFalse(r["alert"])  # 影子模式永不真实告警
            shadow_ids.append(r["alert_id"])
        # 旧阈值会对 0.08 平台误报，人工标 0；新基线不报
        for aid in shadow_ids:
            eng.label_shadow(aid, 0)
        drop = eng.check("d", "m", 0.005, shadow=True)
        eng.label_shadow(drop["alert_id"], 1)  # 真实故障
        report = eng.shadow_report()
        self.assertEqual(report["observations"], 7)
        self.assertLess(report["new_rule"]["false_positive_rate"],
                        report["legacy_threshold"]["false_positive_rate"])
        self.assertEqual(report["new_rule"]["true_positives"], 1)

    def test_shadow_run_has_no_side_effects(self):
        # 影子观测不得改变抑制状态；随后的正式检查仍按正常逻辑告警
        eng = Engine(Config(min_samples=5, window=20, season=None,
                            cold_start_threshold=None))
        for v in [0.1] * 7:
            eng.check("d", "m", v)
        eng.check("d", "m", 0.02, shadow=True)
        s = eng._series[("d", "m")]
        self.assertFalse(s.suppressed)
        r = eng.check("d", "m", 0.02)  # 正式检查：连续确认逻辑不受影子期影响
        self.assertFalse(r["suppressed"])

    # ---- 方向化告警 ---- #
    def test_direction_down_ignores_upward_spike(self):
        eng = Engine(Config(direction="down", min_samples=5, window=20, season=None))
        for v in [0.1, 0.1, 0.1, 0.1, 0.1, 0.1]:
            eng.check("d", "m", v)
        # direction=down：向上的尖峰不告警
        self.assertFalse(eng.check("d", "m", 0.5)["alert"])
        # 向下的断崖仍然告警
        self.assertTrue(eng.check("d", "m", 0.01)["alert"])


if __name__ == "__main__":
    unittest.main()
