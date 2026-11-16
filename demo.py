from vulnticket import report, update

# 第一段：完整流转，修复提交后进入待复测，复测通过才关闭
print(update({"id": "T-1", "vuln": "V-9", "project": "shop"}))
print(update({"id": "T-1", "status": "fixing", "owner": "chen", "operator": "li"}))
print(update({"id": "T-1", "status": "pending_retest", "evidence": "commit abc123", "operator": "chen"}))
print(update({"id": "T-1", "retest": {"result": "pass", "by": "li"}}))
print(update({"id": "T-1", "status": "closed", "operator": "li"}))

# 第二段：复测失败自动退回，重复漏洞合并到已有工单
print(update({"id": "T-2", "vuln": "V-10", "project": "shop"}))
print(update({"id": "T-2", "status": "fixing", "owner": "wang", "operator": "li"}))
print(update({"id": "T-2", "status": "pending_retest", "evidence": "commit def456", "operator": "wang"}))
print(update({"id": "T-2", "retest": {"result": "fail", "by": "li", "note": "仍可复现"}}))
print(update({"id": "T-3", "vuln": "V-10", "project": "shop"}))  # 重复开单，合并到 T-2

data = report("shop")
print("stuck:", data["stuck"])
print("duplicates:", data["duplicates"])
print("reject_reasons:", data["reject_reasons"])
