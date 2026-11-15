# crawlchain

采集链路追踪：一个标识贯穿入队到入库，重试同号，跨进程传号。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

用法：

```python
from crawlchain import start, query
tr = start("task-a")                          # 重试：start("task-a", trace_id=tr["trace"])
with tr.stage("fetch"): ...                   # 子进程：start(task, trace_id=..., attempt=...)
tr.finish(ok=True)
query(tr["trace"])                            # 各阶段耗时、最慢阶段、失败点、错误链
```

链路数据按任务保留最近一天（`CRAWLCHAIN_RETENTION_HOURS` 可配），慢阶段阈值 `CRAWLCHAIN_SLOW_MS` 可配。
