"""对比两版数据契约：双向兼容结论、兼容矩阵、迁移脚本建议。"""

from datacontract2 import ContractHistory, check, export, matrix

contract_v1 = {
    "name": "users",
    "version": 1,
    "fields": {
        "id": "int",
        "name": "text",
        "email": {"type": "text"},
        "status": {"type": "text", "enum": ["active", "banned"], "default": "active"},
    },
}

contract_v2 = {
    "name": "users",
    "version": 2,
    "fields": {
        "id": "text",  # 类型放宽：读兼容、写破坏
        # name 被删除：双向破坏
        "email": {"type": "text", "required": True},  # 可选 -> 必填：读破坏
        "status": {
            "type": "text",
            "enum": ["active"],  # 删除枚举值：读破坏
            "default": "unknown",  # 默认值修改：警告
        },
        "age": {"type": "int", "default": 0},  # 新增可选字段：兼容
    },
}

result = check(contract_v1, contract_v2)

print("== 兼容矩阵 ==")
print(matrix(contract_v1, contract_v2))

print("\n== 读方向 ==")
print("兼容:", result["read"]["compatible"], " 破坏字段:", result["read"]["breaking"])

print("\n== 写方向 ==")
print("兼容:", result["write"]["compatible"], " 破坏字段:", result["write"]["breaking"])

print("\n== 变更明细 ==")
for c in result["changes"]:
    print("[%s][%s] %s -> 建议: %s" % (c["direction"], c["level"], c["message"], c["suggestion"]))

print("\n== 迁移脚本建议 ==")
for line in result["migration_script"]:
    print(line)

export(result, "compat_report.json")
print("\n检查结果已导出到 compat_report.json")

history = ContractHistory()
history.add(contract_v1)
history.add(contract_v2)
print("历史契约数:", len(history), " 最近两版读方向兼容:", history.compare()["read"]["compatible"])
