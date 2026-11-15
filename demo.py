from metricmerge import add, backfill_queue, merge_sketches, recompute, windows

# 准时数据：按事件时间落窗 2026-01-01 / 2026-01-02
print(add({"event_time": "2026-01-01T08:00:00Z", "value": 1.5, "id": "e1"}))
print(add({"event_time": "2026-01-01T09:00:00Z", "value": 2.5, "id": "e2"}))
print(add({"event_time": "2026-01-02T10:00:00Z", "value": 4.0, "id": "e3"}))

# 迟到数据：事件时间属于 01-01，触发整窗重算（撤销旧版本再写新版本）
print(add({"event_time": "2026-01-01T23:00:00Z", "value": 10.0, "id": "e4"}))

# 重复迟到：按 id 去重，不重复累加
print(add({"event_time": "2026-01-01T23:00:00Z", "value": 10.0, "id": "e4"}))

# 幂等重算：窗口干净时 recomputed=False
print(recompute("2026-01-01T00:00:00+00:00"))

# 分位数：两个窗口的草图合并，合并前后基数对账
print(merge_sketches(*[w["sketch"] for w in windows()]))

# 超过保留期（默认 7 天）的迟到数据进入补算队列，不落窗
print(add({"event_time": "2025-12-01T00:00:00Z", "value": 99.0, "id": "e5"}))
print(backfill_queue())
