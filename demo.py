from surfacemap import report, surface

print(surface([
    {"domain": "gw.example.com", "address": "10.2.0.9", "port": 443, "owner": "pay", "source": "cmdb"},
    {"domain": "gw.example.com", "address": "10.2.0.9", "port": 443, "owner": "pay", "source": "scan"},
]))
print(surface([{"domain": "old.example.com", "address": "10.2.0.5", "port": 80, "owner": "pay", "offline": True}]))
print(report()["by_owner"])

# TODO: 第二段演示（实现后补）
