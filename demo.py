from quarantine import export_samples, ingest, quarantine_list, replay, reset

reset()

# 1. 一批数据里混入坏行：好行照常入库，坏行进隔离区，不再整批回滚
rows = [
    {"id": 1, "name": "alice"},
    {"id": 2, "name": "bob", "bad": True},
    {"name": "carol"},
    {"id": 4, "name": "dave"},
]
result = ingest(rows, batch_id="batch-demo")
print("入库结果:", result)

# 2. 隔离区按原因保留原始内容，可按原因导出样本排查
print("隔离记录:", quarantine_list(batch_id="batch-demo"))
print("flagged_bad 样本:", export_samples("flagged_bad"))
print("missing_id 样本:", export_samples("missing_id"))

# 3. 修复坏行后按批重放；(原始批次位置, 修复行) 精确定位，
#    也可直接传修复后的 dict（按隔离顺序对齐，或按 id/内容自动匹配）
fixed_rows = [
    (1, {"id": 2, "name": "bob-fixed"}),
    (2, {"id": 3, "name": "carol-fixed"}),
]
first = replay("batch-demo", fixed_rows=fixed_rows)
print("首次重放:", first)

# 4. 重放幂等：再放一次不会产生重复入库，只计 duplicates
second = replay("batch-demo", fixed_rows=fixed_rows)
print("再次重放:", second)
