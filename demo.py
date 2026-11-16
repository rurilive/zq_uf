from sandboxreport import parse, summary

report = {
    "id": "s-001",
    "behaviors": [
        {"type": "network", "action": "connect 10.0.0.5:443", "seconds": 12},
        {"type": "file", "action": "write /tmp/a.tmp", "seconds": 3},
        {"type": "network", "action": "connect 10.0.0.5:443", "seconds": 9},
    ],
    "static": {"packer": "upx", "entropy": 7.4},
}

print(parse(report)["behaviors"])
print(summary("s-001"))
print(summary("s-001")["by_type"])

# TODO: 第二段演示（实现后补）
