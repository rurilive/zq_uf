from forensics import by_source, report, timeline

EVIDENCE = [
    {"id": "f1", "time": "2024-05-01T09:30+08:00", "source": "file", "subject": "u1", "action": "read"},
    {"id": "l1", "time": "2024-05-01T02:00+00:00", "source": "log", "subject": "u1", "action": "login"},
    {"id": "r1", "time": "2024-05-01T02:00:04+00:00", "source": "registry", "subject": "u1", "action": "login"},
]

out = timeline(EVIDENCE, case="case-7")
print("事件顺序", [item["id"] for item in out["events"]])
print("归一时间", [item["time"] for item in out["events"]])
print("来源", out["sources"])
print("合并条数", out["merged"], "冲突", len(out["conflicts"]), "最大偏差", out["skew"])
print("日志来源事件", len(by_source("log")), "总量", report()["events"])

# 第二段：同一案件追加证据，含偏差超阈值的冲突与缺失时间的记录
EXTRA = [
    {"id": "l2", "time": "2024-05-01T02:00:00+00:00", "source": "log", "subject": "u2", "action": "login"},
    {"id": "r2", "time": "2024-05-01T02:00:04+00:00", "source": "registry", "subject": "u2", "action": "login"},
    {"id": "f2", "time": "2024-05-01T02:00:09+00:00", "source": "file", "subject": "u2", "action": "login"},
    {"id": "x1", "source": "file", "subject": "u2", "action": "read"},
]

out = timeline(EXTRA, case="case-7")
print("案件合并后事件", out["count"], "缺时标注", len(out["untimed"]))
for conflict in out["conflicts"]:
    print("冲突", conflict["subject"], conflict["action"], "偏差", conflict["skew"])
    for entry in conflict["entries"]:
        print("  ", entry["source"], "原始", entry["raw"], "归一", entry["normalized"])
print("案件汇总", report(case="case-7"))
