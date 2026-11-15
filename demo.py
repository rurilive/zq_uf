from skewwatch import configure, history, observe, replay, report

print(observe({"name": "job", "elapsed": 120}))
print(report("job"))

# 第二段：慢任务倾斜检测验证 —— 热点键应落在实际热点分区上
slow = {
    "name": "slow_job", "elapsed": 600,
    "partitions": [
        {"id": 0, "rows": 1000, "elapsed": 20, "keys": {"a": 600, "b": 400}},
        {"id": 1, "rows": 1100, "elapsed": 22, "keys": {"c": 1100}},
        {"id": 2, "rows": 12000, "elapsed": 480, "keys": {"vip": 11500, "d": 500}},
        {"id": 3, "rows": 900, "elapsed": 18, "keys": {"e": 900}},
    ],
}
result = observe(slow)
print(result["skewed"], result["ratio"])
print("hot keys:", [(h["key"], h["ratio"], h["partitions"]) for h in result["hot"]])
print("hot partitions:", [(p["partition"], p["ratio"]) for p in result["hot_partitions"]])
for a in result["advice"]:
    print(a["type"], a["suggest"], "预估收益(s):", a["est_benefit"])

# 验证：热点键 vip 应定位到实际热点分区 2
assert result["hot"][0]["key"] == "vip"
assert result["hot"][0]["partitions"] == [2]
assert result["hot_partitions"][0]["partition"] == 2

# 重跑不覆盖历史；检测周期可配；可回放
configure(period=60)
observe(dict(slow, elapsed=610))  # 周期内，复用上次结果
configure(period=0)
observe(dict(slow, elapsed=620))
print("runs:", report("slow_job")["runs"], "history:", len(history("slow_job")))
print("replay runs:", len(replay("slow_job")))
