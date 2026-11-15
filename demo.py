"""tableaudit 演示：聚合、窗口去重、敏感留痕、下钻、游标、回放与归档。"""

from tableaudit import (
    archive_expired,
    configure,
    drill_down,
    operator_changes,
    query,
    read_archive,
    record,
    replay,
    reset,
)

HOUR = 3600
T0 = 1_700_000_000

reset()
configure(retention_seconds=2 * HOUR, sample_limit=3)

print("== 1. 写入一批操作（含重复与敏感操作） ==")
batch = [
    {"table": "orders", "op": "select", "user": "etl", "time": T0, "rows": 100},
    {"table": "orders", "op": "select", "user": "etl", "time": T0 + 30, "rows": 100},   # 指纹重复
    {"table": "orders", "op": "select", "user": "etl", "time": T0 + 60, "rows": 200},   # 新指纹
    {"table": "orders", "op": "drop", "user": "dba", "time": T0 + 90},                 # 敏感
    {"table": "orders", "op": "drop", "user": "bob", "time": T0 + 120},                # 敏感 + 操作人变更
    {"table": "pay", "op": "insert", "user": "etl", "time": T0 + HOUR},                # 下一小时桶
]
for event in batch:
    r = record(event)
    flag = []
    if r["duplicate"]:
        flag.append("duplicate")
    if r["sensitive"]:
        flag.append("sensitive")
    if r["operator_change"]:
        flag.append("operator_change")
    print(f"  total={r['total']} {event['table']}/{event['op']}/{event['user']}"
          f"{' [' + ','.join(flag) + ']' if flag else ''}")

print("\n== 2. 小时级聚合查询（时间倒序，含去重计数与样本） ==")
for agg in query({"table": "orders"})["hits"]:
    print(f"  {agg['granularity']} bucket={agg['bucket']} "
          f"{agg['op']}/{agg['user']} count={agg['count']} "
          f"unique={agg['unique']} duplicates={agg['duplicates']} "
          f"sensitive={agg['sensitive']} samples={len(agg['samples'])}")

print("\n== 3. 天级聚合 ==")
for agg in query({"granularity": "day"})["hits"]:
    print(f"  day bucket={agg['bucket']} {agg['table']}/{agg['op']}/"
          f"{agg['user']} count={agg['count']}")

print("\n== 4. 敏感操作原文单独全量保留 ==")
sensitive = query({"op": "drop"}, include_raw=True)["hits"][0]
print(f"  raw_count={sensitive['raw_count']} 原文条数={len(sensitive['raw'])}")

print("\n== 5. 操作人变更 ==")
for ch in operator_changes(table="orders", op="drop"):
    print(f"  {ch['table']}/{ch['op']}: {ch['from_user']} -> {ch['to_user']}")

print("\n== 6. 游标翻页（limit=2） ==")
page = query(limit=2)
print(f"  第1页 {len(page['hits'])} 条, has_more={page['has_more']}")
page = query(limit=2, cursor=page["cursor"])
print(f"  第2页 {len(page['hits'])} 条, has_more={page['has_more']}")

print("\n== 7. 按表+天 下钻到小时 ==")
for agg in drill_down("orders", day=T0):
    print(f"  hour bucket={agg['bucket']} {agg['op']}/{agg['user']} "
          f"count={agg['count']}")

print("\n== 8. 聚合结果回放（敏感全量、普通样本、时间正序） ==")
for entry in replay():
    if entry["kind"] == "operator_change":
        ch = entry["change"]
        print(f"  [change] {ch['table']}/{ch['op']} "
              f"{ch['from_user']} -> {ch['to_user']} @ {ch['time']}")
    else:
        e = entry["event"]
        print(f"  [{entry['retained']}] {e['table']}/{e['op']}/{e['user']} "
              f"@ {e['time']}")

print("\n== 9. 保留期 2 小时，过期归档 ==")
archived = archive_expired(now=T0 + 3 * HOUR)
print(f"  本次归档 {len(archived)} 条，归档库累计 {len(read_archive())} 条")
print(f"  归档后 orders 可查聚合数: {len(query({'table': 'orders'})['hits'])}")
