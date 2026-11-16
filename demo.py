from permaudit import (
    audit,
    changes,
    grant,
    reclaim,
    recheck,
    report,
    reset,
)

ACCOUNT = {
    "id": "u100",
    "system": "billing",
    "role": "support",
    "status": "left",
    "permissions": ["invoice.read", "invoice.refund", "user.export"],
    "used": {"invoice.read": 12},
}


def section_one():
    """原有演示：单账号审计、授权、汇总报告。"""
    print(audit(ACCOUNT))
    print(grant("u100", "audit.read", actor="sec-bob"))
    print(report())


def section_two():
    """完整流程：异常账号、长期未使用、按系统查看、影响范围、收回、复核。"""
    reset()

    # 一批账号快照：离职、转岗、长期未登录、长期未使用权限
    audit({
        "id": "u100", "system": "billing", "role": "support", "status": "left",
        "permissions": ["invoice.read", "invoice.refund", "user.export"],
        "used": {"invoice.read": 12}, "last_login": 95,
    })
    audit({
        "id": "u200", "system": "billing", "role": "cashier",
        "status": "transferred",
        "permissions": ["invoice.read", "invoice.write"],
        "used": {"invoice.read": 2}, "last_login": 40,
    })
    audit({
        "id": "u300", "system": "billing", "role": "cashier", "status": "active",
        "permissions": ["invoice.read", "report.export"],
        "used": {"invoice.read": 5},
        "granted_day": 1, "now": 60, "last_login": 3,
    })
    audit({
        "id": "u400", "system": "crm", "role": "sales", "status": "active",
        "permissions": ["lead.read"],
        "used": {"lead.read": 100}, "last_login": 80,
    })

    print("== 管理员只看 billing 系统，待收回清单按系统/岗位分组 ==")
    billing = report(system="billing")
    print("abnormal:", [a["account"] for a in billing["abnormal"]])
    print("reclaim groups:", list(billing["reclaim"]["billing"]))
    print("reclaim_count:", billing["reclaim_count"])
    print("长期未登录:", billing["inactive"])

    print("== 收回前先看影响范围（dry_run 不落库）==")
    preview = reclaim("u300", dry_run=True)
    print(preview["impact"])

    print("== 执行收回，变更保留人和时间 ==")
    done = reclaim("u300", actor="sec-bob")
    print("removed:", done["removed"], "remaining:", done["impact"]["remaining"])

    print("== 收回后只复核变化的账号 ==")
    print("pending_recheck:", report()["pending_recheck"])
    print("recheck:", recheck("u300")["reviewed"])
    print("pending_recheck after:", report()["pending_recheck"])

    print("== 权限变更记录按时间排序 ==")
    for item in changes():
        print(item["seq"], item["time"], item["account"],
              item["action"], item["permission"], "by", item["actor"])


if __name__ == "__main__":
    section_one()
    section_two()
