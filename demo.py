from honeypot import cluster, report

events = [
    {"ip": "203.0.113.7", "path": "/admin", "at": 10, "ua": "nikto"},
    {"ip": "203.0.113.7", "path": "/admin", "at": 12, "ua": "nikto"},
    {"ip": "198.51.100.4", "path": "/.env", "at": 30, "ua": "gobuster"},
]

print(cluster(events))
print(report()["top"])

# TODO: 第二段演示（实现后补）
