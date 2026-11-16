from dataclass import discover, hits, report

TABLE = {
    "name": "crm.users",
    "columns": [
        {"name": "phone", "comment": "手机号", "values": ["13800001111", "13900002222"]},
        {"name": "email_addr", "comment": "邮箱", "values": ["a@example.com"]},
        {"name": "nick", "comment": "昵称", "values": ["alice"]},
    ],
}

result = discover(TABLE)
print("sensitive", result["sensitive"])
print(result["columns"])
print(hits("crm.users"))
print(report())

# TODO: 第二段演示（实现后补）
