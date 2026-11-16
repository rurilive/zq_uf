from forensics import by_source, report, timeline

EVIDENCE = [
    {"id": "f1", "time": "2024-05-01T09:30+08:00", "source": "file", "action": "read"},
    {"id": "l1", "time": "2024-05-01T02:00+00:00", "source": "log", "action": "login"},
    {"id": "r1", "time": "2024-05-01T02:00:04+00:00", "source": "registry", "action": "login"},
]

out = timeline(EVIDENCE)
print("事件顺序", [item["id"] for item in out["events"]])
print("来源", out["sources"])
print("合并条数", out["merged"], "冲突", len(out["conflicts"]))
print("日志来源事件", len(by_source("log")), "总量", report()["events"])

# TODO: 第二段演示（实现后补）
