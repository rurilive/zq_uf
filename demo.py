from metricrollup2 import add, configure, query, reset, stats

reset()
configure(window_size=60, retention_windows=10, sketch_capacity=32)

# 正常事件流：按事件时间落窗
for ts, v in [(0, 1.0), (10, 2.0), (70, 5.0), (80, 7.0)]:
    print(add({"dim": "shop", "ts": ts, "value": v}))

# 迟到数据：ts=5 落在已走过的窗口 [0,60)，触发该窗口重算
print(add({"dim": "shop", "ts": 5, "value": 100.0}))

print(query("shop", 0))    # 窗口边界 + recomputes=1 + 草图
print(query("shop", 60))
print(query("*", 0))       # 上卷，与下钻一致
print(stats())
