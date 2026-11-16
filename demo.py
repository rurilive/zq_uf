from alertfold import emit, report

print(emit([
    {"reason": "disk full", "host": "db-01", "site": "bj", "time": "10:01"},
    {"reason": "disk full", "host": "db-02", "site": "bj", "time": "10:02"},
]))
print(emit([{"reason": "cpu high", "host": "web-01", "site": "sh", "time": "10:05"}]))
print(report()["by_reason"])

# TODO: 第二段演示（实现后补）
