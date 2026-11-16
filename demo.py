from surfacemap import changes, confirm, report, reset, stale, surface

reset()

JAN5 = "2026-01-05T09:00:00+00:00"
JAN20 = "2026-01-20T09:00:00+00:00"
FEB10 = "2026-02-10T09:00:00+00:00"

# 同一入口从两个来源登记，合并成一条。
print(surface([
    {"domain": "gw.example.com", "address": "10.2.0.9", "port": 443,
     "owner": "pay", "source": "cmdb", "discovered_at": JAN5},
    {"domain": "gw.example.com", "address": "10.2.0.9", "port": 443,
     "owner": "pay", "source": "scan"},
], now=JAN5))

# 下线只标失效、不删除；之后重新登记即恢复。
print(surface([{"domain": "old.example.com", "address": "10.2.0.5",
                "port": 80, "owner": "pay", "offline": True}], now=JAN20))

# 归属不明和第三方托管分别落位。
print(surface([
    {"domain": "api.example.com", "address": "10.2.0.10", "port": 443},
    {"domain": "cdn.vendor.net", "address": "10.9.0.1", "port": 443,
     "owner": "web", "third_party": True},
], now=JAN20))

rep = report(now=FEB10)
print(rep["by_owner"])          # 按业务分组
print(rep["pending_entries"])   # 无法确认归属
print(rep["third_party_entries"])
print(stale(days=20, now=FEB10))  # 长期没确认

# 重新确认仍在的入口。
print(confirm([{"domain": "gw.example.com", "address": "10.2.0.9"}], now=FEB10))

# 按月导出变化，可按业务筛选。
print(changes("2026-01")["counts"])
print(surface([{"domain": "old.example.com", "address": "10.2.0.5",
                "port": 80, "owner": "pay"}], now=FEB10))
print(changes("2026-02")["counts"])
print(report(owner="pay", now=FEB10)["by_owner"])
