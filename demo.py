from lineagerepair import audit, confirm, infer, register, registry, reset, rollback

reset()

# 既有手工登记：同一源指向另一目标，用于演示冲突保留
register(("raw.orders", "dwd.orders_legacy"), "bob")

logs = [
    # 跨批次复现 -> 高置信度；与登记边同源不同目标 -> 冲突待确认
    {"job": "etl_orders", "batch": "20261112", "read": "raw.orders", "write": "dwd.orders"},
    {"job": "etl_orders", "batch": "20261113", "read": "raw.orders", "write": "dwd.orders"},
    # 同批次两条 -> 中置信度；read/write 也支持列表
    {"job": "etl_customer", "batch": "20261112", "read": ["raw.cust"], "write": ["dwd.cust"]},
    {"job": "etl_customer", "batch": "20261112", "read": ["raw.cust"], "write": ["dwd.cust"]},
    # 仅一条日志 -> 证据不足，不产出
    {"job": "etl_oneoff", "batch": "20261112", "read": "raw.tmp", "write": "dwd.tmp"},
]

inferred = infer(logs)
print("== 推断边（边 / 置信度 / 状态 / 证据）==")
for item in inferred:
    jobs = sorted({e["job"] for e in item["evidence"]})
    print(item["edge"], item["confidence"], item["level"], item["status"], "jobs=", jobs)

print("\n== 冲突与待确认项 ==")
for item in inferred:
    if item["conflicts_with"]:
        print("冲突:", item["edge"], "vs 已登记", item["conflicts_with"], "-> 两侧保留，待人工确认")
print("待确认:", [item["edge"] for item in inferred])

target = inferred[0]["edge"]
print("\n== 人工确认", target, "==")
print(confirm(target, "alice"))
print("登记快照:", registry())
print("审计动作:", [(entry["id"], entry["action"], entry["operator"]) for entry in audit()])

print("\n== 回滚确认 ==")
print(rollback(target, "alice"))
print("回滚后登记:", registry())
print("审计动作:", [(entry["id"], entry["action"], entry["operator"]) for entry in audit()])
