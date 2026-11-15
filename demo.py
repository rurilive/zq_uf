"""maskctl 演示：入仓脱敏、受控还原、规则审批与清单导出。"""
from maskctl import MaskCtl, Rule, RuleRegistry

registry = RuleRegistry(approvers={"lead-wang"}, oncall={"oncall-zhang"})
ctl = MaskCtl(registry=registry)

# 1. 字段规则走审批：phone 保留前3后4，address 保留前6，name 声明为明文
changes = [
    registry.submit_change("dev-li", "users", "phone", rule=Rule(keep_prefix=3, keep_suffix=4)),
    registry.submit_change("dev-li", "users", "address", rule=Rule(keep_prefix=6)),
    registry.submit_change("dev-li", "users", "name", plain=True),
]
for cid in changes:
    registry.approve(cid, "lead-wang")

# 2. 采集入仓：按规则处理；没配规则的 id_card 走默认策略全遮蔽
row = {
    "phone": "13800000000",
    "address": "北京市海淀区中关村大街1号",
    "name": "张三",
    "id_card": "110101199001011234",
}
result = ctl.mask(row, table="users")
print("落库数据:", result["row"])
print("已处理字段:", result["masked"])

# 3. 联调还原：未授权拿不到原值；授权后凭令牌还原，操作写审计
print("未授权还原:", ctl.reveal(result["token"], requester="dev-li")["revealed"])
ctl.grant_reveal("dev-li", "users", granted_by="lead-wang")
print("授权后还原:", ctl.reveal(result["token"], requester="dev-li")["row"])

# 4. 处理清单按表导出
print("处理清单:\n" + ctl.manifest.export("users"))

# 5. 审批记录按表汇总，值班同学可查看、可导出
print("审批汇总:", registry.approval_summary(viewer="oncall-zhang"))
print("审批记录:\n" + registry.export_approvals(viewer="oncall-zhang"))
