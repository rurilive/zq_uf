from subdomain import diff, enumerate, load, report, restore, wildcards

ROWS = [
    {"name": "www.example.com", "ip": "10.0.0.5", "status": "ok"},
    {"name": "web.example.com", "ip": "10.0.0.5", "status": "ok"},
    {"name": "api.example.com", "ip": "10.0.0.9", "status": "ok"},
    {"name": "cdn.example.com", "ip": "10.0.0.99", "status": "wildcard"},
    {"name": "old.example.com", "ip": "", "status": "fail"},
]

print("登记候选", load("example.com", ROWS)["loaded"])
out = enumerate("example.com")
print("有效入口", out["total"], "别名", out["aliases"])
print("通配命中", out["wildcard"], "无效", out["invalid"])
print("上报覆盖", report("example.com")["valid"])

# 第二段：新一轮枚举，已确认无效的被跳过，只看新增部分
again = load("example.com", [
    {"name": "old.example.com", "ip": "", "status": "fail"},
    {"name": "new.example.com", "ip": "10.0.0.7", "status": "ok"},
])
print("第", again["round"], "轮，跳过已确认无效", again["skipped"])
print("新增入口", diff("example.com")["entries"])

# 通配记录保留原始解析结果，误判可单独恢复
print("通配原始记录", wildcards("example.com"))
restore("example.com", "cdn.example.com")
print("恢复后有效入口", enumerate("example.com")["total"])
