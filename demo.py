from schemagray import apply, plan

change = {
    "tables": [
        {"name": "dim_region", "access": "read", "task": "bi"},
        {"name": "dim_channel", "access": "read", "task": "bi"},
        {"name": "orders", "access": "write", "task": "sync"},
        {"name": "users", "access": "write", "task": "sync"},
    ],
    "batch_size": 1,
    "approver": "dba-chen",
    "summary": "add column vip_level",
}

p = plan(change)
print("batches:", p["batches"])

# 第一批只读表正常，第二批写入表 orders 错误率超标 -> 停批并回滚已放量表
metrics = {"orders": {"error_rate": 0.5, "latency_ms": 20.0}}
stopped = apply(p, observe=lambda batch, _i: metrics.get(batch[0], {}))
print("paused:", stopped["paused"], "| reason:", stopped["stop_reason"])
print("rolled_back:", stopped["rolled_back"], "| record:", stopped["record"])

# 修复后从停批处继续，只影响剩余批次
resumed = apply({**p, "resume_from": stopped["resume_from"]})
print("resumed applied:", resumed["applied"], "| paused:", resumed["paused"])
