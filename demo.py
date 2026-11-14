"""模拟上游故障：几百条告警聚合成一条，恢复发汇总，静默到期自动恢复。"""

import json

import metricalert as ma

ma.reset()

# 可控时钟，保证输出可复现。
now = [1020.0]
ma.configure(clock=lambda: now[0])


def brief(record):
    keys = (
        "type", "key", "reason", "action", "count", "affected_jobs",
        "first_ts", "last_ts", "silent_until", "trigger",
    )
    return json.dumps({k: record[k] for k in keys if k in record}, ensure_ascii=False)


print("== 上游故障：300 条告警 ==")
for i in range(300):
    now[0] = 1020.0 + i * 0.1  # 全部落在同一个 60s 窗口内
    ma.emit({"job": "report-%d" % i, "reason": "upstream", "dimension": "latency"})

summary = ma.aggregates()[0]
print("投递通知数:", len(ma.aggregates()))
print("影响任务数:", summary["affected_jobs"], "/ 告警条数:", summary["count"])
print("首末时间:", summary["first_ts"], "->", summary["last_ts"])

print("\n== 另一根因：磁盘（独立聚合键）==")
now[0] = 1055.0
ma.emit({"job": "checkout-1", "reason": "disk"})
ma.emit({"job": "checkout-2", "reason": "disk"})
print("聚合键:", [a["key"] for a in ma.aggregates()])

print("\n== 恢复：上游故障 ==")
result = ma.resolve("upstream|report", now=1070)
print(brief({**result, "type": "recovery"}))

print("\n== 静默：磁盘告警进入静默期（到期时间 1110）==")
before = len(ma.aggregates())
ma.silence("disk|checkout", seconds=40, now=1070)
ma.emit({"job": "checkout-3", "reason": "disk", "ts": 1071})
print("静默期内新增投递:", len(ma.aggregates()) - before)
print("抑制记录:", brief(ma.suppressions()[-1]))

print("\n== 静默到期：自动恢复并留抑制记录 ==")
now[0] = 1111.0
ma.tick()
print("恢复汇总:", brief(ma.recoveries()[-1]))
expired = [r for r in ma.suppressions() if r["reason"] == "silence_expired"]
print("到期抑制记录:", brief(expired[-1]))

print("\n== 输出记录 ==")
print("聚合", len(ma.aggregates()), "抑制", len(ma.suppressions()), "恢复", len(ma.recoveries()))
