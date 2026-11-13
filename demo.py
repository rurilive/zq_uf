"""三个数据集、三种策略：生成计划 -> 干跑 -> 确认后分批执行。"""
import json
import os
import tempfile
import time

from retention import Dataset, apply, plan

DAY = 86400
NOW = time.time()


def write_record(root, name, age_days, tags=()):
    record = {"timestamp": NOW - age_days * DAY, "tags": list(tags)}
    with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
        json.dump(record, fh)


with tempfile.TemporaryDirectory() as tmp:
    roots = {name: os.path.join(tmp, name)
             for name in ("orders", "logs", "metrics")}
    for root in roots.values():
        os.makedirs(root)

    # orders：合规留 90 天，至少留 2 份，legal_hold 豁免
    write_record(roots["orders"], "o1.json", 120)
    write_record(roots["orders"], "o2.json", 100, tags=["legal_hold"])
    write_record(roots["orders"], "o3.json", 95)
    write_record(roots["orders"], "o4.json", 10)
    write_record(roots["orders"], "o5.json", 200)
    # logs：业务留 30 天
    write_record(roots["logs"], "l1.json", 45)
    write_record(roots["logs"], "l2.json", 20)
    # metrics：只留 7 天，archive 豁免
    write_record(roots["metrics"], "m1.json", 30)
    write_record(roots["metrics"], "m2.json", 15, tags=["archive"])
    write_record(roots["metrics"], "m3.json", 3)

    datasets = [
        Dataset("orders", roots["orders"], days=90, min_keep=2,
                exempt_tags=["legal_hold"], batch_size=2),
        Dataset("logs", roots["logs"], days=30),
        Dataset("metrics", roots["metrics"], days=7,
                exempt_tags=["archive"]),
    ]

    manifest_dir = os.path.join(tmp, "manifests")
    audit_dir = os.path.join(tmp, "audit")

    deletion_plan = plan(datasets=datasets, manifest_dir=manifest_dir)
    for entry in deletion_plan.entries:
        print("[%s] days=%d delete=%d keep=%d" % (
            entry["dataset"], entry["days"],
            len(entry["delete"]), len(entry["kept"])))
    print("manifest:", os.listdir(manifest_dir))

    would = apply(deletion_plan, dry_run=True, audit_dir=audit_dir)
    print("dry run, would remove:", [os.path.basename(p) for p in would])

    removed = apply(deletion_plan, confirm=True, audit_dir=audit_dir)
    print("confirmed, removed:", [os.path.basename(p) for p in removed])
    print("audit records:", sorted(os.listdir(audit_dir)))

