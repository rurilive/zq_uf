from schemaversion import audit, register, report, resolve, rollback

# v1：初始结构
register({"version": 1, "fields": {"id": "int"}})

# v2：向前兼容——新增带默认值字段，无需审批
register(
    {
        "version": 2,
        "fields": {
            "id": "int",
            "note": {"type": "str", "default": ""},
        },
    }
)

# v3：破坏性变更——id 类型 int -> str，需显式标记并登记审批人
register(
    {"version": 3, "fields": {"id": "str", "note": {"type": "str", "default": ""}}},
    breaking=True,
    approver="alice",
)

print("当前版本:", resolve())
print("按版本解析 v1:", resolve(1))

print(rollback(1))
print("回滚后当前版本:", resolve())
print("回滚后旧版本 v3 仍可读:", resolve(3))

print()
print(report())
print()
print("审计记录:", audit())
