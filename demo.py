"""一次过期清理演示：保留期 30 天，沿血缘级联，租约保护，分批提交并审计。"""

from retention import AuditLog, apply, plan

DATASET = "orders"
PARTITIONS = [
    {"name": "2026-09-01", "age_days": 74},  # 过期：级联删副本和缓存
    {"name": "2026-09-02", "age_days": 73},  # 过期：下游副本有活跃租约，跳过
    {"name": "2026-10-20", "age_days": 25},  # 在保留期内，保留
]
LINEAGE = {
    ("orders", "2026-09-01"): [
        ("orders_replica", "2026-09-01"),
        ("orders_cache", "2026-09-01"),
    ],
    ("orders", "2026-09-02"): [("orders_replica", "2026-09-02")],
}
LEASES = {("orders_replica", "2026-09-02"): {"id": "lease-42", "owner": "reload-job"}}


def main():
    audit = AuditLog()
    cleanup_plan = plan(DATASET, PARTITIONS, keep_days=30,
                        lineage=LINEAGE, leases=LEASES)
    print("== plan ==")
    print(cleanup_plan)

    result = apply(
        cleanup_plan,
        batch_size=2,
        interval_seconds=0,
        delete_func=lambda refs: print("delete batch:", refs),
        audit_log=audit,
    )
    print("== apply ==")
    print({
        "status": result["status"],
        "deleted": result["deleted"],
        "batches": result["batches"],
        "skipped": result["skipped"],
    })
    print("== audit ==")
    for entry in audit.entries:
        print(entry)


if __name__ == "__main__":
    main()
