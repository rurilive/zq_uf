# backfillplan

分片回填：按时间和主键切片，动态并发限速，断点重试，幂等写入。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 切片口径

- `plan(range, shards)`：range 必含 `start`/`end`，主键半开区间 `[start, end)`。
- 可选 `time_start`/`time_end`/`time_step`（秒）：先按时间分桶，桶内再按主键切。
- 单分片最小跨度 `MIN_SHARD_SPAN=5`，区间太小会合并，实际分片数可能少于请求值。
- 后一个时间桶的分片依赖前一桶全部完成；优先级按时间桶从早到晚、主键从小到大递减。

## 限速口径

- `run(plan, workers)`：workers 是并发上限，实际生效 `min(workers, 分片数)`。
- 初始并发为上限一半；上游有 `pressure()` 则 >0.7 减半、<0.3 加一，
  否则按 fetch 平均延迟超 `latency_threshold` 减半、低于 1/4 阈值加一。
- 失败分片从 `checkpoint` 续跑，最多 `max_retries` 次；sink 按键去重保证幂等。
- `Job.pause()/resume()/status()` 支持暂停、恢复和进度查询。
