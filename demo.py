"""过期分区清理演示：干跑 -> 保存计划 -> 分批级联删除 -> 失败停批回滚。"""

import json
import os
import tempfile

from partretention import apply, load_plan, plan, rollback, save_plan

CATALOG = {
    "lineage": {"orders": ["orders_daily", "orders_agg"], "orders_daily": ["orders_hourly"]},
    "partitions": {
        "orders_daily": ["2025-01-01", "2025-01-02"],
        "orders_agg": ["2025-01-01"],
        "orders_hourly": ["2025-01-01"],
    },
    "leases": {"orders_hourly": ["lease-42"]},      # 活跃租约：跳过
    "schema_changed": ["orders_agg"],                # 结构变更：跳过并告警
}

dataset = {
    "name": "orders",
    "days": ["2025-01-01", "2025-01-02", "2025-01-03"],
    "catalog": CATALOG,
    "batch_size": 2,
}

# 第一段：干跑输出影响范围，保存计划后再执行
p = plan(dataset)
print("干跑影响范围:", json.dumps(p["impact"], ensure_ascii=False))
print("跳过:", json.dumps(p["skipped"], ensure_ascii=False))
print("告警:", p["warnings"])

workdir = tempfile.mkdtemp(prefix="partretention-")
plan_path = save_plan(p, os.path.join(workdir, "plan.json"))
result = apply(load_plan(plan_path),
               archive_dir=os.path.join(workdir, "archive"),
               audit_path=os.path.join(workdir, "audit.jsonl"))
print("删除:", result["deleted"])
print("级联:", result["cascaded"])
print("批次:", [(r["index"], r["status"]) for r in result["batch_records"]])
print("归档:", result["archived"])
print("审计汇总:", json.dumps(result["audit_summary"], ensure_ascii=False))

# 第二段：删除失败即停批，失败批次单独回滚
bad_catalog = dict(CATALOG, fail_on=["orders:2025-01-02"])
bad = plan(dict(dataset, catalog=bad_catalog, batch_size=1))
failed = apply(bad)
print("失败批次:", failed["failed_batch"], "已执行批次:", failed["batches"])
restored = rollback(failed, failed["failed_batch"])
print("回滚:", restored["rolled_back"], "剩余删除:", restored["deleted"])
