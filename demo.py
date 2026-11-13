"""跨月分区查询演示：时间范围裁剪 + 条件下推 + 缺失分区报错。"""
import json
import os
import tempfile

from partquery import MissingPartitionError, ROOT, plan_query, query

os.chdir(tempfile.mkdtemp())
base = os.path.join(ROOT, "orders")
os.makedirs(base)

fixture = {
    "2026-01-31": [{"city": "sh", "amount": 5}, {"city": "bj", "amount": 20}],
    "2026-02-01": [{"city": "sh", "amount": 15}, {"city": "bj", "amount": 25}],
    "2026-02-15": [{"city": "sh", "amount": 30}],
    "2026-03-01": [{"city": "bj", "amount": 8}],
}
for day, rows in fixture.items():
    with open(os.path.join(base, f"dt={day}.jsonl"), "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

print("=== 跨月时间范围裁剪（2026-02 月内）===")
february = query("orders", {"dt": {"gte": "2026-02-01", "lt": "2026-03-01"}})
print(json.dumps({key: february[key] for key in
                  ("partitions", "scanned", "scanned_total", "row_count", "elapsed_ms")},
                 ensure_ascii=False))
assert february["partitions"] == ["dt=2026-02-01.jsonl", "dt=2026-02-15.jsonl"]
assert february["scanned"] == 2 and february["scanned_total"] == 4
assert february["row_count"] == 3 and len(february["rows"]) == 3

print("=== 裁剪 + 行级条件下推（2 月起，city=sh）===")
pushed = query("orders", {"dt": {"gte": "2026-02-01"}, "city": "sh"})
print(json.dumps({key: pushed[key] for key in
                  ("partitions", "scanned", "row_count", "rows", "elapsed_ms")},
                 ensure_ascii=False))
assert pushed["scanned"] == 3 and pushed["row_count"] == 4
assert [row["amount"] for row in pushed["rows"]] == [15, 30]

print("=== 裁剪计划可序列化，重启后可复现 ===")
plan = plan_query("orders", {"dt": {"gte": "2026-02-01"}})
print(json.dumps(json.loads(json.dumps(plan)), ensure_ascii=False))
assert plan["partitions"] == [
    "dt=2026-02-01.jsonl", "dt=2026-02-15.jsonl", "dt=2026-03-01.jsonl"]

print("=== 分区缺失：显式报错，不静默返回空 ===")
try:
    query("orders", {"dt": "2026-12-31"})
except MissingPartitionError as exc:
    print(type(exc).__name__ + ": " + str(exc))
else:
    raise AssertionError("缺失分区必须抛 MissingPartitionError")

print("OK: 全部断言通过")
