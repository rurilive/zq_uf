"""模拟乱序到达：水位推进、窗口清理、迟到判重、过期与淘汰。"""

from dedupwindow import advance, configure, dumps, loads, reset, seen, stats

reset()
configure(window_size=20, allowed_lateness=5, max_size=8)

# 乱序事件流：(key, 事件时间)
stream = [("a", 1), ("b", 2), ("a", 1), ("c", 3), ("d", 10),
          ("b", 2), ("e", 11), ("c", 3)]
for key, ts in stream:
    print(f"seen({key!r}, {ts}) ->", "dup" if seen(key, ts) else "new")
print("advance(10) state:", advance(10))

print("late dup:", seen("d", 9))    # 迟到 1，在允许范围内，仍判重
print("expired:", seen("z", 1))     # 迟到 9 > 5，超出范围，标记过期
print("stats:", stats())

# 突发流量触发状态上限，按最旧 ts 淘汰
for i in range(20):
    seen(f"k{i}", 20 + i)
print("after burst:", stats())

# 序列化 -> 模拟重启 -> 恢复水位和窗口
blob = dumps()
reset()
loads(blob)
print("after restart:", stats())
print("dup after restart:", seen("k19", 39))
