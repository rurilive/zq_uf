"""端到端演示：基线告警、缓变确认、误报抑制、趋势、回测、影子运行。"""
from qtrend2 import Config, Engine

# 第一段：保留旧接口 check(dataset, {"value": ...})
from qtrend2 import check, trend

print(check("orders", {"value": 0.08}))
print(check("orders", {"value": 0.01}))
print(trend("orders"))

print("\n================ 基线驱动告警 ================")
eng = Engine(Config(window=14, season=7, min_samples=5,
                    cold_start_threshold=None, direction="down"))

# 1) 业务量台阶上移：旧固定阈值会天天误报，基线适应后静默
print("-- 业务量整体上移（0.02 -> 0.08 平台），正常波动不告警 --")
for v in [0.078, 0.081, 0.079, 0.082, 0.08, 0.077, 0.081, 0.08]:
    r = eng.check("orders", "error_rate", v)
    print(f"value={v:<6} alert={r['alert']!s:<5} baseline_median="
          f"{r['baseline']['median'] if r['baseline'] else '-'}")

# 2) 断崖式下降：立即告警，携带基线/偏离/样本/历史片段
print("\n-- 断崖式下降，立即告警并带历史对比 --")
r = eng.check("orders", "error_rate", 0.01)
print(f"alert={r['alert']} reason={r['reason']} "
      f"baseline={r['baseline']['median']:.4f} z={r['z']} "
          f"relative_change={r['relative_change']:.1%}")
print(f"samples(n={r['baseline']['n']})={[round(x, 3) for x in r['samples']]}")
print(f"history_snippet={r['history']}")

# 3) 值班判定为误报（业务变更），标记后不再重复提醒
print("\n-- 标记误报，重复告警被抑制 --")
eng.mark_false_positive("orders", "error_rate", alert_id=r["alert_id"],
                        note="大促口径变更")
again = eng.check("orders", "error_rate", 0.012)
print(f"再次出现同类值: alert={again['alert']} suppressed={again['suppressed']} "
      f"reason={again['reason']}")

# 4) 恢复正常后自动重新武装
print("\n-- 恢复基线后重新武装 --")
eng.check("orders", "error_rate", 0.08)
re_alert = eng.check("orders", "error_rate", 0.01)
print(f"再次断崖: alert={re_alert['alert']} reason={re_alert['reason']}")

# 5) 缓慢下降：连续三个周期确认
print("\n-- 缓慢下降，连续多周期确认 --")
eng2 = Engine(Config(window=20, season=None, min_samples=5,
                     cold_start_threshold=None, drift_consecutive=3))
for v in [0.1] * 7:
    eng2.check("pay", "fail_rate", v)
for v in (0.085, 0.072, 0.061):
    r = eng2.check("pay", "fail_rate", v)
    print(f"value={v:<6} alert={r['alert']!s:<5} "
          f"consecutive_breaches={r['consecutive_breaches']} reason={r['reason']}")
print("趋势:", {k: eng2.trend("pay", "fail_rate")[k]
                for k in ("direction", "slope", "relative_change")})

# 6) 回测：同一份数据，新基线 vs 旧固定阈值，可扫阈值
print("\n-- 回测：同一份数据对比新旧规则 --")
points = [{"dataset": "d", "value": v, "label": 0}
          for v in [0.08, 0.081, 0.079, 0.082, 0.08, 0.078, 0.081, 0.08]]
points += [{"dataset": "d", "value": 0.005, "label": 1}]
bt_engine = Engine(Config(window=20, season=None, min_samples=5,
                          cold_start_threshold=None))
report = bt_engine.backtest(points)
print("旧固定阈值:", report["legacy_threshold"])
print("新基线规则:", report["new_rule"])
print("\n-- 阈值网格回测（节选）--")
for row in bt_engine.sweep_thresholds(points, deviation_zs=(2.0, 3.0),
                                      drift_consecutives=(2, 3)):
    print(row)

# 7) 影子运行：只记录不告警，与人工判断对比
print("\n-- 影子运行：新规则只记录不告警，人工标签对比误报率 --")
shadow = Engine(Config(window=20, season=None, min_samples=5,
                       cold_start_threshold=None))
for v in [0.08, 0.081, 0.079, 0.082, 0.08, 0.078]:
    rec = shadow.check("d", "m", v, shadow=True)
    shadow.label_shadow(rec["alert_id"], 0)   # 人工：正常（旧阈值却会报）
rec = shadow.check("d", "m", 0.005, shadow=True)
shadow.label_shadow(rec["alert_id"], 1)       # 人工：真实故障
print(shadow.shadow_report())
