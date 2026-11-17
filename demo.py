from permaudit import audit, grant, reclaim, report, review, changes, reset

# 第一段：离职账号，全部权限进入待收回清单。
reset()

ACCOUNT = {
    "id": "u100",
    "system": "billing",
    "role": "support",
    "status": "left",
    "permissions": ["invoice.read", "invoice.refund", "user.export"],
    "used": {"invoice.read": 12},
}

print("== 离职账号审计 ==")
print(audit(ACCOUNT))
print(grant("u100", "audit.read", actor="admin.li", time=101))
print(report())

# 第二段：在职/转岗/休眠三类账号的完整审计、收回和复核流程。
print("\n== 批量审计 ==")
# 在职账号：invoice.refund 闲置 120 天，超过 STALE_DAYS(90)，进待收回。
audit({
    "id": "u200",
    "system": "billing",
    "role": "support",
    "status": "active",
    "permissions": ["invoice.read", "invoice.refund"],
    "used": {"invoice.read": 5, "invoice.refund": 120},
    "last_login": 6,
})
# 转岗账号：现岗位 support 只需要 invoice.read，其余按原岗位残留收回。
audit({
    "id": "u201",
    "system": "billing",
    "role": "support",
    "status": "transferred",
    "permissions": ["invoice.read", "invoice.refund", "user.export"],
    "used": {"invoice.read": 8, "invoice.refund": 40, "user.export": 41},
    "last_login": 9,
    "role_permissions": {"support": ["invoice.read"], "finance": ["invoice.refund"]},
})
# 休眠账号：200 天未登录，单独统计。
audit({
    "id": "u202",
    "system": "crm",
    "role": "sales",
    "status": "active",
    "permissions": ["lead.read"],
    "used": {"lead.read": 200},
    "last_login": 200,
})

body = report()
print("按系统的账号数:", body["systems"])
print("异常账号:", [entry["account"] for entry in body["abnormal"]])
print("长期未登录:", body["dormant"])
print("待收回（按系统/岗位分组）:")
for system, roles in body["reclaim_list"].items():
    for role, entries in roles.items():
        by_account = {}
        for entry in entries:
            by_account.setdefault(entry["account"], []).append(
                (entry["permission"], entry["reason"])
            )
        detail = {
            account: sorted(items) for account, items in sorted(by_account.items())
        }
        print(f"  {system}/{role}: {detail}")

# 收回前先看影响范围，再执行；u200 的清单缺省全收。
print("\n== 收回 u200 的闲置权限 ==")
print(reclaim("u200", actor="admin.li", time=130)["impact"])

# 收回后只复核发生变化的账号，且每个账号只能复核一次。
print("\n== 复核 ==")
print("待复核账号:", report()["review"])
print(review("u200", actor="auditor.wang", time=131))
print("复核后待复核:", report()["review"])

# 变更记录按时间排序，复盘时可以只看某个账号。
print("\n== 变更记录（按时间） ==")
for record in changes():
    print(f"  t={record['time']} {record['action']} {record['account']} "
          f"{record['permission']} by {record['actor']}")
