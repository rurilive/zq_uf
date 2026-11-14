# crawlbarrier

采集阶段屏障：按批次和分片协调，全部到达才放行下游，
任一分片失败则整批失败并触发补偿，等待超时返回未到达分片。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 屏障口径

- `expect(batch, *shards)` 登记批次期望到达的分片；未登记时 `wait` 立即返回已到达分片（兼容旧行为）。
- `arrive(batch, shard, ok=True, compensate=None)` 记录分片到达，返回已到达分片数；重复到达是 no-op，以首次到达为准。
- `wait(batch, timeout=None)` 阻塞到全部期望分片到达、失败或超时；`timeout=None` 表示一直等待。
- 返回 `WaitResult`（list 子类）：成功/失败时内容是已到达分片，超时时内容是未到达分片。
- 附带属性：`ok` 是否放行、`status` 各分片状态（ok/failed/missing）、`timed_out` 超时未到达列表、`failed` 失败分片、`compensation` 补偿结果。

## 补偿口径

- 任一分片以 `ok=False` 到达即整批失败，立即触发补偿并放行所有等待者。
- 补偿按写入（到达）顺序的逆序执行，只执行已登记的 `compensate` 回调。
- 补偿幂等：每批只执行一次，重复到达、重复等待不会重复触发。
- 补偿返回值记入 `WaitResult.compensation`，异常捕获为 "error: ..." 不中断其余补偿。
