from surfacemap import changes, report, surface

print(surface([
    {"domain": "gw.example.com", "address": "10.2.0.9", "port": 443, "owner": "pay", "source": "cmdb"},
    {"domain": "gw.example.com", "address": "10.2.0.9", "port": 443, "owner": "pay", "source": "scan"},
]))
print(surface([{"domain": "old.example.com", "address": "10.2.0.5", "port": 80, "owner": "pay", "offline": True}]))
print(report()["by_owner"])

# 第二段：同一入口合并成一条，失效入口保留，第三方单独标注，待确认单列。
surface([
    {"domain": "cdn.example.com", "address": "10.2.0.11", "third_party": True, "owner": "pay", "source": "scan"},
    {"domain": "mystery.example.com", "address": "10.2.0.12", "source": "scan"},
])

data = report()
print("counts:", data["counts"])
print("active:", [row["domain"] for row in data["active"]])
print("offline:", [(row["domain"], row["offline_at"]) for row in data["offline"]])
print("pending:", [row["domain"] for row in data["pending"]])
print("third_party:", [row["domain"] for row in data["third_party"]])
print("sources:", data["sources"])

# 变化部分按月对比、单独导出。
print("changes:", changes())
