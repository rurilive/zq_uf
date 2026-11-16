from covmatrix import report, scan

print(scan({"host": "10.0.0.5"}))
print(scan({"host": "10.0.0.5", "throttle": "429 too many requests"}))
print(report()["targets"])

# TODO: 第二段演示（实现后补）
