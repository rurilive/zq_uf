# rateadapt

按站点自适应的采集限速：慢涨快降，速率与并发共用一个预算。

```python
from rateadapt import acquire, feedback, status, records, export_records

slot = acquire("shop")                 # 额度不足时 ok=False，带 wait / hint
if slot["ok"]:
    status_code, rtt = fetch_shop()    # 自己的采集调用
    feedback("shop", status_code, rtt) # 成功缓慢提速；429 折半降速并冷却

status("shop")        # 当前速度、剩余额度、冷却结束时间、最近一次调整原因
records("shop")       # 最近一小时（默认）的提速 / 降速 / 冷却结束记录
export_records("shop", fmt="csv")   # 按站点导出，保留最近一天；kind="all" 连额度不足一起
```

`acquire` 返回 `rate`、`remaining`、`remaining_concurrency`、`remaining_tokens`、
`cooldown_until`、`cooldown_in`、`last_adjust`。额度不足时 `reason` 为
`cooldown` / `concurrency_full` / `quota_exhausted`，`wait` 是建议等待秒数；
`acquire(site, wait=True, max_wait=5)` 会排队等待，超时仍返回提示。

调参都在 `rateadapt/rate.py` 顶部：`SUCCESS_THRESHOLD`（连续多少次成功提速）、
`SPEEDUP_PCT` / `SPEEDUP_MAX`（提速幅度与单次上限）、`SLOWDOWN_FACTOR`（429 后保留比例）、
`BUDGET` / `MAX_CONCURRENCY`（速率与并发共用的预算）、`RETENTION_SECONDS`（留档时长）。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
