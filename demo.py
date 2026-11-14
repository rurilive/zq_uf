"""模拟扩容迁移：老存储写满，新增 storage2，按规则切流并迁移老分区。"""

import partitionmap
from partitionmap import migrate, route

partitionmap.configure(state_dir=".partitionmap-demo")

print("== 扩容前：默认路径 ==")
print(route("orders", "dt=2026-01-01"))

print("\n== 登记规则：新分区落 storage2（含一条低优先级冲突规则） ==")
partitionmap.add_rule({"name": "orders-to-storage2", "dataset": "orders",
                       "start": "2026-02-01", "target": "storage2/{dataset}/{partition}",
                       "priority": 10})
partitionmap.add_rule({"name": "catch-all", "dataset": "orders",
                       "target": "storage1/{dataset}/{partition}", "priority": 1})
r = route("orders", "dt=2026-03-01")
print("路由:", r, "| 命中规则:", r.rule["name"])
if r.conflict:
    two = [x["name"] for x in r.conflict["rules"]]
    print("冲突:", two, "| 胜者:", r.conflict["winner"], "| 依据:", r.conflict["resolved_by"])

print("\n== 迁移老分区到 storage2：prepare 生成映射并双读 ==")
plan = {"dataset": "orders",
        "partitions": ["dt=2026-01-01", "dt=2026-01-02"], "to": "storage2"}
result = migrate(dict(plan, action="prepare"))
print("迁移结果:", result["status"], "moved =", result["moved"])
for part, entry in result["mapping"].items():
    print("  映射", entry["from"], "->", entry["to"])
r = route("orders", "dt=2026-01-01")
print("双读:", r, "| 旧路径兜底:", r.old_path)

print("\n== switch：停旧路径 ==")
result = migrate({"action": "switch", "id": result["id"]})
print("迁移结果:", result["status"], "moved =", result["moved"])
r = route("orders", "dt=2026-01-01")
print("路由:", r, "| 双读:", r.dual_read)

print("\n== 回滚演示：重新 prepare 后 rollback ==")
mid = migrate(dict(plan, action="prepare"))["id"]
migrate({"action": "rollback", "id": mid})
print("回滚后路由:", route("orders", "dt=2026-01-01"))

print("\n== 落盘记录 ==")
print("规则表:", [x["name"] for x in partitionmap.rules()])
print("迁移记录:", [(x["action"], x["status"]) for x in partitionmap.migrations()])
