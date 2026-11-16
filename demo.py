from permaudit import audit, grant, report

ACCOUNT = {
    "id": "u100",
    "system": "billing",
    "role": "support",
    "status": "left",
    "permissions": ["invoice.read", "invoice.refund", "user.export"],
    "used": {"invoice.read": 12},
}

print(audit(ACCOUNT))
print(grant("u100", "audit.read"))
print(report())

# TODO: 第二段演示（实现后补）
