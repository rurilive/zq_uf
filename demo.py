"""fieldpatch 端到端演示：试运行 -> 正式修补 -> 统计 -> 回滚 -> 回放 -> 导出。"""

from fieldpatch import (
    patch, report, run_batch, rollback, replay, export_patches, reset,
)

# 行级：缺一个必填字段只隔离该字段，不丢整行
print("== 单行修补 ==")
print(patch({"title": "x"}, {"required": ["title", "price"]}))

reset()

schema = {
    "fields": {
        "title": {"type": "string", "required": True},
        "price": {"type": "number", "required": True},
        "count": {"type": "number"},                       # 可空数字，缺失补 0
        "status": {"type": "enum", "values": ["new", "used"],
                   "degrade": "unknown"},
    }
}
rows = [
    {"id": 1, "title": "完整记录", "price": 10, "count": 2, "status": "new"},
    {"id": 2, "title": "缺必填price"},                              # -> 隔离
    {"id": 3, "title": "需修补", "price": 5, "count": "7",
     "status": "越界值"},                                            # -> 收敛+降级
]

# 1) 先在单个站点试运行，确认补齐结果能被下游读取，但不落库
print("\n== 试运行(dry_run)，不落库 ==")
trial = run_batch(rows, schema, site="site-A", day="2026-11-16",
                  dry_run=True)
print("summary:", trial["summary"], "| batch_id:", trial["batch_id"])
print("report batches（应为0）:", report()["batches"])

# 2) 正式修补：同批字段齐全的记录不受问题记录影响
print("\n== 正式修补 ==")
out = run_batch(rows, schema, site="site-A", day="2026-11-16")
bid = out["batch_id"]
print("batch_id:", bid)
print("summary:", out["summary"])
print("alerts:", out["alerts"])

# 3) 按天 + 站点统计：修补、隔离、来源、缺失比例
print("\n== 按天/站点统计 ==")
rep = report()
print("缺失比例:", rep["missing_ratio"],
      "| 必填缺失:", rep["missing_required"],
      "| 来源分布:", rep["sources"])
print("明细:", rep["by_day_site"])

# 4) 导出逐字段留痕（带来源与原值），值班同学可按站点查看
print("\n== 导出 CSV（前两行） ==")
print("\n".join(export_patches(site="site-A", fmt="csv").splitlines()[:3]))

# 5) 按批次回滚：恢复原值、解除隔离，统计随之归零
print("\n== 回滚 ==")
print(rollback(bid))
print("回滚后 patched（应为0）:", report()["patched"])

# 6) 回放已回滚的批次，重新应用修补
print("\n== 回放 ==")
print(replay(bid))
print("回放后 patched（应大于0）:", report()["patched"])
