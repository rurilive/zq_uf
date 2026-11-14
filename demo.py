"""模拟一段分片回填：源表重算 -> 撤销重写 -> 对账停批 -> 幂等重跑 -> 记录查询。"""

from metricbackfill import Ledger, MetricStore, apply, plan

# 源表：6 天 × 2 个地区的原始事件
source = MetricStore(
    {"time": day, "dims": {"region": region}, "value": day * 10}
    for day in range(1, 7)
    for region in ("cn", "us")
)

# 目标表：已有的旧指标，us 第 5-6 天的值是坏的
store = MetricStore(
    {"time": day, "dims": {"region": region}, "value": day * 10}
    for day in range(1, 7)
    for region in ("cn", "us")
)
for row in store.rows:
    if row["dims"]["region"] == "us" and row["time"] in (5, 6):
        row["value"] = 99999

ledger = Ledger()
p = plan({
    "start": 1, "end": 6,
    "shard_days": 2,                 # 时间切片：每 2 天一片
    "dims": {"region": ["cn", "us"]},  # 维度切片：按地区
    "threshold": 100,                # 对账阈值：行数或值差异超过即停批
})
print("分片: %d 片" % len(p["shards"]))

result = apply(p, store=store, source=source, ledger=ledger)
for r in result["results"]:
    print("shard %(shard_id)s [%(start)s,%(end)s] %(dims)s -> %(status)s "
          "row_diff=%(row_diff)s value_diff=%(value_diff)s" % r)
print("停批:", result["stopped"], "| 原因:", result["stop_reason"])

# 幂等：修正阈值后重跑同一 plan，先撤销再写，不会翻倍
p["threshold"] = None
again = apply(p, store=store, source=source, ledger=ledger)
print("重跑写入行数:", again["rows"], "| 目标表总行数:", len(store.rows))
assert len(store.rows) == 12, "重复回填不能翻倍"

# 回填记录按区间查询
print("第 3-4 天的回填记录:", [(r["start"], r["status"]) for r in ledger.query(start=3, end=4)])
