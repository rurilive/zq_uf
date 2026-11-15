from sitehealth import daily, history, replay, report, score, watchlist

print(report("shop", {"ok": True, "rtt": 0.2}))
print(report("shop", {"ok": False, "status": 429}))
print(score("shop"))

# 第二段：故障快降 → 观察名单 → 连续恢复后移出
DAY = 86400.0
t0 = 1_700_000_000.0

snap = reason_at_drop = None
for i in range(4):  # 连续失败，快速降分
    snap = report("api", {"ok": False, "status": 503}, ts=t0 + i * 60)
    if i == 2:  # 第 3 次连续失败，分项直接对齐目标分
        reason_at_drop = snap["last_change"]["reason"]
print("故障后:", snap["score"], snap["trend"], reason_at_drop)
print("观察名单:", [w["site"] for w in watchlist()], "派单:", snap["dispatch"])

for d in range(3):  # 连续三天恢复健康，缓慢回升
    for i in range(12):
        snap = report("api", {"ok": True, "rtt": 0.2}, ts=t0 + (d + 1) * DAY + i * 300)
print("恢复后:", snap["score"], "派单:", snap["dispatch"])
print("按天回看:", [(d["day"], d["last"]) for d in daily("api")])
print("回放一致:", replay("api")["final"]["score"] == score("api")["score"])
print("历史条数:", len(history("api")))
