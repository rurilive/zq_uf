from aggretract import Aggregator

# allowed_lateness=300：事件时间落后水位线 5 分钟以内仍按原窗口接收。
agg = Aggregator(allowed_lateness=300)

first_batch = [
    {"key": "orders", "id": "o1", "version": 1, "value": 100,
     "event_time": "2026-01-02T10:00:00Z"},
    {"key": "orders", "id": "o2", "version": 1, "value": 50,
     "event_time": "2026-01-02T10:05:00Z"},
]
agg.apply(first_batch)

# 同一批重复投递：按 id+version 去重，不翻倍。
agg.apply(first_batch)

# 更新：o1 金额从 100 改为 80（新版本覆盖旧版本）。
agg.apply([{"key": "orders", "id": "o1", "version": 2, "value": 80,
            "event_time": "2026-01-02T10:00:00Z"}])

# 撤回：o2 从聚合中扣减。
agg.apply([{"key": "orders", "id": "o2", "version": 2, "op": "retract",
            "event_time": "2026-01-02T10:05:00Z"}])

# 迟到但在容忍窗口内：归到 10:00 所在的日窗口。
agg.apply([{"key": "orders", "id": "o3", "version": 1, "value": 20,
            "event_time": "2026-01-02T10:01:00Z"}])

# 迟到且超过容忍度：丢弃并计入 late_count。
agg.apply([{"key": "orders", "id": "o4", "version": 1, "value": 999,
            "event_time": "2026-01-01T09:00:00Z"}])

stats = agg.stats("orders")
print("orders 聚合值:", agg.query("orders"))
print("orders 对账:", {k: v for k, v in stats.items() if k != "windows"})
print("窗口明细:", stats["windows"])

assert agg.query("orders") == 100
assert stats["detail_count"] == 2
assert stats["late_count"] == 1
assert stats["retracted_count"] == 1
