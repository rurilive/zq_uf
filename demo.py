import json

from changebatch import (
    apply,
    export_rollbacks,
    journal,
    pending,
    resume,
    snapshot,
    watermark,
)


def reconcile():
    table = snapshot()
    return {"rows": len(table), "sum": sum(table.values())}


def show(title, value):
    print(title, json.dumps(value, ensure_ascii=False, sort_keys=True))


# 1. 对账任务：第一批成功，推进水线
show("apply b1    ->", apply({"id": "b1", "seq": 1,
                              "rows": [{"key": "a", "value": 100},
                                       {"key": "b", "value": 200}]}))
show("reconcile   ->", reconcile())

# 2. 第二批中途失败：整批回滚，数字与回滚前一致
before = reconcile()
show("apply b2    ->", apply({"id": "b2", "seq": 2,
                              "rows": [{"key": "a", "value": 1},
                                       {"key": "c", "value": 3}],
                              "fail_times": 1}))
show("reconcile   ->", reconcile())
print("== 回滚后对账一致:", reconcile() == before, "==\n")

# 3. 第三批：前面有失败批，越位阻塞，不留缺口
show("apply b3    ->", apply({"id": "b3", "seq": 3,
                              "rows": [{"key": "d", "value": 4}]}))
print("pending     ->", list(pending()), "watermark =", watermark(), "\n")

# 4. 重复提交同一批：只应用一次
show("re-apply b1 ->", apply({"id": "b1", "seq": 1,
                              "rows": [{"key": "a", "value": 100}]}))

# 5. 恢复：从最后成功批（b1）继续，补 b2，再放行 b3
show("resume      ->", resume("recover-1"))
show("final table ->", snapshot())
print("watermark   ->", watermark(), "pending ->", list(pending()), "\n")

# 6. 冲突保留两侧：a 已有 1，新批想写 999
show("apply b4    ->", apply({"id": "b4", "seq": 4,
                              "rows": [{"key": "a", "value": 999}]}))

# 7. 审计日志与回滚明细（可导出回看）
print()
for entry in journal():
    print("audit:", entry["event"], entry.get("batch"),
          entry.get("reason") or entry.get("error", ""))
print("rollback export ->",
      json.dumps(export_rollbacks(), ensure_ascii=False, indent=2))
