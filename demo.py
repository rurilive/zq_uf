from dataclass import adjust, discover, export, hits, report, rescan, samples

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

# 第二段：人工调整 + 按库导出
STAFF = {
    "name": "hr.staff",
    "columns": [
        {"name": "c1", "comment": "", "values": ["11010119900307777X"]},
        {"name": "card_no", "comment": "银行卡", "values": ["6222020200112233445"]},
    ],
}
discover(STAFF)

# 认定错了可以人工调整，记住后只重扫受影响的表
adjust("crm.users", "email_addr", level="高")
print("rescanned", rescan())
print(samples("crm.users", "phone"))
print(report())

for row in export("crm"):
    print(row)
