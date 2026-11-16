from pcapflow import analyze, load, report

PACKETS = [
    {"no": 1, "src": "10.0.0.5", "dst": "10.0.0.9", "dport": 445, "proto": "tcp"},
    {"no": 2, "src": "10.0.0.5", "dst": "10.0.0.9", "dport": 445, "proto": "tcp"},
    {"no": 3, "src": "10.0.0.5", "dst": "203.0.113.7", "dport": 443, "proto": "tcp"},
]

print("包数量", load("cap1", PACKETS)["packets"])
out = analyze("cap1")
print("命中会话", out["sessions"], "类型", out["hits"][0]["type"])
print("证据包序号", [hit["packet"] for hit in out["hits"]])
print("规则版本", out["rule_version"] or "空", "主机数", len(report()["hosts"]))

# TODO: 第二段演示（实现后补）
