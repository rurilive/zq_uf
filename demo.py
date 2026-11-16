from honeypot import cluster, report

# 一批地址用相同工具、相同节奏扫相同路径：归为一个扫描组
events = [
    {"ip": "203.0.113.7", "path": "/admin", "at": 10, "ua": "nikto"},
    {"ip": "203.0.113.7", "path": "/.env", "at": 20, "ua": "nikto"},
    {"ip": "198.51.100.4", "path": "/admin", "at": 12, "ua": "nikto"},
    {"ip": "198.51.100.4", "path": "/.env", "at": 22, "ua": "nikto"},
    {"ip": "192.0.2.9", "path": "/admin", "at": 11, "ua": "nikto"},
    {"ip": "192.0.2.9", "path": "/.env", "at": 21, "ua": "nikto"},
    # 混在批量扫描里的针对性探测：偏离组特征，单独标出
    {"ip": "192.0.2.9", "path": "/backup.sql", "at": 95, "ua": "nikto"},
]

result = cluster(events)
for group in result["groups"]:
    print(group["id"], group["features"]["tool"], group["features"]["paths"],
          "sources=%d" % group["source_count"],
          "count=%d" % group["count"],
          "first=%s last=%s" % (group["first"], group["last"]))
print("outliers:", [(o["ip"], o["path"], o["reason"]) for o in result["outliers"]])

# 下一轮：换了新地址的同一批扫描仍归入原组（prior 沿用组 id），
# 人工调整（merge/move）也可以通过 adjust 传入
nxt = cluster(events + [
    {"ip": "203.0.113.99", "path": "/admin", "at": 200, "ua": "nikto"},
    {"ip": "203.0.113.99", "path": "/.env", "at": 210, "ua": "nikto"},
], prior=result)
print("daily:", nxt["daily"])
print(report()["groups"][0]["id"], "沿用上一轮组 id，sources=%d"
      % report()["groups"][0]["source_count"])
