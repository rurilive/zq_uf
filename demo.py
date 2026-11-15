from backfill2 import apply, export, plan, query, replay, retry

rows = [{"time": t, "channel": c, "value": t * (1 if c == "a" else 2)}
        for t in range(1, 31) for c in ("a", "b")]

# 切片计划：按 7 天 × 渠道切片，保存复用
p = plan({"start": 1, "end": 30}, slice_size=7,
         dimensions={"channel": ["a", "b"]}, threshold=0.05)
print("slices:", len(p["slices"]), "saved:", p["saved"])

# 先试跑一个小时间片，确认撤销重写后指标不翻倍
trial = apply(p, rows, trial=True)
print("trial doubled:", trial["double_check"]["doubled"])

# 全量回填；重复执行先撤销再写，不会翻倍
run = apply(p, rows)
run2 = apply(p, rows)
print("retracted:", run2["retracted"], "stopped:", run2["stopped"])

# 源数据剧变，对账差异超阈值停批，之后单独重试失败分片
changed = [dict(r, value=r["value"] * 5) for r in rows]
stopped = apply(p, changed)
print("stop_reason:", stopped["stop_reason"])
print("retry slices:", len(retry(stopped["run_id"])["slices"]))

# 查询、导出、回放
print(query(stopped["run_id"])["diff"])
print(export(stopped["run_id"])[:120], "...")
print("replay stopped:", replay(stopped["run_id"])["stopped"])
