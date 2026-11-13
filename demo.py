import json

from latejoin import join

FACTS = [
    {"id": 1, "event_time": "2024-01-15", "v": 10},
    {"id": 1, "event_time": "2024-02-15", "v": 20},
    {"id": 1, "event_time": "2024-03-05", "v": 30},
    {"id": 2, "event_time": "2024-02-20", "v": 40},
    {"id": None, "v": 50},
]
DIMS = [
    {"id": 1, "effective_time": "2024-01-01", "name": "旧"},
    {"id": 1, "effective_time": "2024-03-01", "name": "新"},
    # 迟到：生效时间 2024-02-01 早于同主键已到达的 2024-03-01
    {"id": 1, "effective_time": "2024-02-01", "name": "迟到修订"},
]

result = join(FACTS, DIMS)
print("== 关联结果 ==")
for row in result:
    print(json.dumps(row, ensure_ascii=False))
print("== 统计 ==")
stats = {k: v for k, v in result.stats.items() if k != "backfill_batches"}
print(json.dumps(stats, ensure_ascii=False))
print("== 回补批次 ==")
for batch in result.stats["backfill_batches"]:
    print(json.dumps(batch, ensure_ascii=False))
