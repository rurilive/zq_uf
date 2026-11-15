"""演示：按事件时间上卷、迟到只重算所在窗口、分位用草图合并。"""

from datetime import datetime, timezone

from metricroll3 import RollStore

DAY = 86400
store = RollStore(window_seconds=60, ttl_seconds=3600, alpha=0.01)
base = int(datetime(2026, 11, 15, 0, 0, tzinfo=timezone.utc).timestamp())


def at(seconds):
    return datetime.fromtimestamp(base + seconds, timezone.utc).isoformat()


# 1) 一个跨越两个窗口的批次：按事件时间自动拆开，计数/求和分别落到各自窗口。
print("== 跨窗批次 ==")
for row in store.add_batch([
    {"dim": "shop", "event_time": at(70), "value": 9.0},
    {"dim": "shop", "event_time": at(10), "values": [1.0, 2.0]},
    {"dim": "shop", "event_time": at(40), "value": 3.0},
]):
    print(row)

# 2) 迟到事件：水位线已越过第一个窗口，只重算那一个窗口。
print("\n== 迟到事件 ==")
late = store.add({"dim": "shop", "event_time": at(50), "value": 4.0})
print("recomputed:", late["recomputed"], "window:", late["window_start"], late["window_end"])

# 3) 上卷查询：返回窗口起止、计数、求和、分位结果与误差范围。
print("\n== 上卷查询 ==")
rollup = store.query("shop", start=at(0), end=at(120))
for key in ("window_start", "window_end", "count", "sum", "recomputed"):
    print(f"  {key}: {rollup[key]}")
for label, value in rollup["quantiles"].items():
    bound = rollup["error"][label]
    print(f"  {label}: {value:.4f}  ±{bound['relative']:.0%}"
          f"  [{bound['low']:.4f}, {bound['high']:.4f}]")

# 4) 下钻与上卷一致：逐窗口合并后的分布等于上卷分布。
print("\n== 下钻 vs 上卷 ==")
for row in store.drilldown("shop", start=at(0), end=at(120)):
    print(" ", row["window_start"], "count=", row["count"], "sum=", row["sum"],
          "p50=", round(row["quantiles"]["p50"], 4))

# 5) 与手工计算对比：窗口 1 的样本是 [1,2,3,4]，sum=10，中位数取最近秩 = 2。
print("\n== 手工对比 ==")
print("  手工: count=4 sum=10 p50=2")
first = store.query("shop", start=at(0), end=at(60))
print(f"  实现: count={first['count']} sum={first['sum']} "
      f"p50={first['quantiles']['p50']:.4f}")

# 6) 过期窗口自动清理：推高水位线后，旧窗口查询为空并标记 expired。
print("\n== 过期清理 ==")
store.add({"dim": "shop", "event_time": at(3 * DAY), "value": 1.0})
old = store.query("shop", start=at(0), end=at(60))
print("  expired:", old["expired"], "count:", old["count"])
