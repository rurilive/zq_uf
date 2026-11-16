from subdomain import enumerate, load, report

ROWS = [
    {"name": "www.example.com", "ip": "10.0.0.5", "status": "ok"},
    {"name": "web.example.com", "ip": "10.0.0.5", "status": "ok"},
    {"name": "api.example.com", "ip": "10.0.0.9", "status": "ok"},
    {"name": "old.example.com", "ip": "", "status": "fail"},
]

print("登记候选", load("example.com", ROWS)["loaded"])
out = enumerate("example.com")
print("有效入口", out["total"], "别名", out["aliases"])
print("通配命中", out["wildcard"], "无效", out["invalid"])
print("上报覆盖", report("example.com")["valid"])

# TODO: 第二段演示（实现后补）
