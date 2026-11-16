from alertfold import emit, report, reset

print(emit([
    {"reason": "disk full", "host": "db-01", "site": "bj", "time": "10:01"},
    {"reason": "disk full", "host": "db-02", "site": "bj", "time": "10:02"},
]))
print(emit([{"reason": "cpu high", "host": "web-01", "site": "sh", "time": "10:05"}]))
print(report()["by_reason"])

# 第二段：恢复统一关闭，重复出现另开一条
emit([{"reason": "disk full", "host": "db-01", "site": "bj", "time": "11:00", "status": "resolved"}])
emit([{"reason": "disk full", "host": "db-03", "site": "sh", "time": "23:30"}])
for entry in report()["entries"]:
    print(entry["reason"], entry["status"], entry["host_count"],
          entry["first_time"], "->", entry["last_time"], entry["sites"])
reset()
