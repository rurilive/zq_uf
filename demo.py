"""模拟上游失败：300 条下游告警聚合成 1 条，恢复时发 1 条汇总。"""

from alertmerge import configure, emit, reset, sent

BASE = 1_700_000_000.0

reset()

# 第一段：默认配置，逐条透传（兼容旧行为）
for index in range(4):
    emit({"job": f"job-{index}", "state": "firing", "reason": "upstream failed"})
print(len(sent()), "条告警")

# 第二段：开启聚合，模拟上游失败打爆下游
reset()
configure(window_seconds=600, escalate_after=50)

for index in range(300):
    emit({"job": f"etl-{index:03d}", "state": "firing",
          "reason": "upstream failed", "group": "nightly",
          "time": BASE + index})
for index in range(300):
    emit({"job": f"etl-{index:03d}", "state": "recovered",
          "reason": "upstream failed", "group": "nightly",
          "time": BASE + 600 + index})

rows = sent()
print(f"下游告警 600 条，实际发送 {len(rows)} 条")
for row in rows:
    print(row["state"], "影响任务", row["jobs"], "首", row["first"], "末", row["last"])
