"""模拟采集落库 + 联调还原流程。"""

from maskctl import ALERTS, AUDIT_LOG, export_inventory, mask, reveal

# 1. 采集侧：脱敏后落库，令牌交给联调人
row = {"phone": "13800000000", "address": "北京市海淀区中关村大街1号", "name": "张三"}
res = mask(row, table="users", requester="collector-01")
print("脱敏落库:", res["row"])
print("脱敏字段:", res["masked"])
print("还原令牌:", res["token"])

# 2. 联调侧：凭令牌还原原值
back = reveal(res["row"], res["token"], requester="dev-li")
print("联调还原:", back["row"], "revealed =", back["revealed"])

# 3. 无有效令牌：拒绝 + 告警 + 记录请求人
bad = reveal(res["row"], "token-1", requester="dev-li")
print("非法还原:", bad["revealed"], "告警:", ALERTS[-1])

# 4. 按表导出脱敏清单
print("脱敏清单:", export_inventory())

# 5. 审计流水
for entry in AUDIT_LOG:
    print("审计:", entry)
