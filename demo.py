from datetime import datetime, timedelta, timezone

from retentiontag import apply, audit, effective_tags, exempt, lease, link, propagation, tag

# 血缘：customers -> orders -> order_items -> order_report；orders -> invoices
link("customers", "orders")
link("orders", "order_items", "invoices")
link("order_items", "order_report")

print("== 1. 标签继承与收紧 ==")
tag("customers", "keep365")
print("orders keep7 ->", tag("orders", "keep7"))
print("order_items 尝试放宽 keep30 ->", tag("order_items", "keep30"))
print("order_report 收紧 keep3 ->", tag("order_report", "keep3"))
for record in propagation():
    note = f" (拒绝 {record['rejected']}，保持继承)" if record.get("rejected") else ""
    print(f"  {record['table']}: {record['tag']} 来源={record['source']} 层={record['layer']}{note}")

print("\n== 2. 豁免：到期时间 + 审批人 ==")
now = datetime.now(timezone.utc)
exempt("invoices", now + timedelta(days=3), "alice", "月末对账")
exempt("order_report", now - timedelta(days=1), "bob", "已过期，自动失效")
print("生效标签:", effective_tags())

print("\n== 3. 租约 ==")
lease("orders", now + timedelta(days=1))

print("\n== 4. 级联清理 ==")
result = apply("orders")
print("目标生效标签:", result["tag"])
print("子树生效标签:", result["effective"])
print("豁免状态:")
for record in result["exemptions"]:
    print(f"  {record['table']}: {record['status']} approver={record['approver']}")
print("依赖顺序（叶子先）:", result["dependency_order"])
for batch in result["batches"]:
    print(f"  批次 {batch['batch']} (深度 {batch['depth']}): {batch['tables']}")
print("跳过:", result["skipped"])

print("\n== 5. 标签审计 ==")
for entry in audit():
    print(f"  {entry['table']}: {entry['old']} -> {entry['new']} @ {entry['at']}")
