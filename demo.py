from failclass import SiteLedger, classify, policy

# 第一段：分类与策略
print(classify("timeout"))
print(classify("HTTP 404", {"status_code": 404, "headers": {"server": "nginx"}}))
print(policy("banned"))

# 第二段：站点级账本
ledger = SiteLedger(site_budget=2)
r1 = ledger.record("site-a", "task-1", "read timeout")
r2 = ledger.record("site-a", "task-2", "HTTP 403",
                   {"status_code": 403, "headers": {"retry-after": "600"}})
r3 = ledger.record("site-a", "task-3", "HTTP 404", {"status_code": 404})
print(r1["action"], r1["remaining"])          # retry 1
print(r2["action"], r2["switch_exit"])        # cooldown True
print(ledger.site_available("site-a"))        # False（冷却中，到期自动恢复）
print(r3["action"])                           # give_up
print(ledger.breakdown("site-a"))             # 失败构成
print(ledger.export_review("site-a"))         # 复核清单导出
print(ledger.requeue("site-a", "task-3"))     # 复核后重新入队，不占预算
