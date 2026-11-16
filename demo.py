"""子域枚举收敛演示：入口归并、泛解析识别、无效跳过、多轮对比、误判恢复。

直接运行：python demo.py
"""

from subdomain import diff, enumerate, load, report, reset, restore

ROWS = [
    {"name": "www.example.com", "ip": "10.0.0.5", "status": "ok"},
    {"name": "web.example.com", "ip": "10.0.0.5", "status": "ok"},
    {"name": "api.example.com", "ip": "10.0.0.9", "status": "ok"},
    {"name": "old.example.com", "ip": "", "status": "fail"},
    {"name": "slow.example.com", "ip": "", "status": "timeout"},
]

reset("example.com")
print("登记候选", load("example.com", ROWS)["loaded"])
out = enumerate("example.com")
print("有效入口", out["total"], "别名", out["aliases"])
print("通配命中", out["wildcard"], "无效", out["invalid"])
for entry in out["entries"]:
    print("   入口", entry["ip"], entry["names"])
rep = report("example.com")
print("上报覆盖", rep["valid"], "候选", rep["candidates"],
      "覆盖率", rep["coverage_percent"])

# 第二段：泛解析单列、无效跳过、两轮只比新增、误判恢复。
print()
WILD = [
    {"name": "www.wild.test", "ip": "10.1.0.1", "status": "ok"},
    {"name": "rand1.wild.test", "ip": "10.1.0.9", "status": "wildcard"},
    {"name": "rand2.wild.test", "ip": "10.1.0.9", "status": "wildcard"},
    {"name": "gone.wild.test", "ip": "", "status": "fail"},
]
reset("wild.test")
load("wild.test", WILD)
print("通配名单独标出", enumerate("wild.test")["wildcard_names"])
print("通配保留原始记录", enumerate("wild.test")["wildcard_records"][0]["raw"])

load("wild.test", WILD + [{"name": "new.wild.test", "ip": "10.1.0.1", "status": "ok"}])
second = enumerate("wild.test")
print("第二轮", "跳过已确认无效", second["skipped"], "无效", second["invalid"])
print("只比新增", diff("wild.test")["added_names"])
print("只报新增", enumerate("wild.test", only_new=True)["new_names"])

result = restore("wild.test", "rand1.wild.test")
print("误判恢复", result["name"], result["from"], result["ips"])
print("恢复后通配命中", enumerate("wild.test")["wildcard"],
      "有效入口", enumerate("wild.test")["total"])
print("恢复记录", report("wild.test")["restored"])
