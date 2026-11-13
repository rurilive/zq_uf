"""演示：跨月历史分区的带保护批处理清理。"""
import json
import os
import tempfile
import time

from partitiongc import apply, plan

DAY = 86400


def make_partition(root, name, age_days, dataset="default", lease=None):
    path = os.path.join(root, name)
    os.makedirs(path)
    with open(os.path.join(path, "partition.json"), "w",
              encoding="utf-8") as handle:
        json.dump({
            "dataset": dataset,
            "event_time": time.time() - age_days * DAY,
        }, handle)
    if lease is not None:
        lease_dir = os.path.join(path, ".leases")
        os.makedirs(lease_dir)
        with open(os.path.join(lease_dir, "downstream-etl.json"), "w",
                  encoding="utf-8") as handle:
            json.dump({
                "reader": "downstream-etl",
                "expires_at": time.time() + lease,
            }, handle)
    return path


with tempfile.TemporaryDirectory() as tmp:
    # 目录名完全不规整，且跨越 1、2、3 月，时间只认 partition.json
    partitions = [
        make_partition(tmp, "jan-backup-x", 95),
        make_partition(tmp, "feb-shard-42", 60),
        make_partition(tmp, "march-part-7", 40,
                       lease=3600),  # 活跃租约，应跳过
        make_partition(tmp, "recent-ish", 5),   # 保留期内，不进计划
        make_partition(tmp, "cold-archive-zz", 70, dataset="cold"),
    ]
    audit_path = os.path.join(tmp, "audit.jsonl")

    planned = plan(
        tmp,
        keep_days={"cold": 100, "default": 30},
        batch_size=2,
        audit_path=audit_path,
    )
    print("planned:", list(planned))
    print("batches:", [[entry["name"] for entry in batch]
                       for batch in planned.batches])

    result = apply(planned)
    print("removed:", list(result))
    print("skipped:", [(item["path"], item["reason"])
                       for item in result.skipped])

    with open(audit_path, encoding="utf-8") as handle:
        print("audit events:", [json.loads(line)["event"]
                                for line in handle if line.strip()])
