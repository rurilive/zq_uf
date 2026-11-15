# crawlcap

按站点成功率阶梯调并发，并发与站点配额联动，纯标准库，线程安全。

## 阶段

- `ramp`：连续成功 4 次升一档（4→8→16），任何失败清零连胜。
- `cooldown`：连续失败 3 次回退一档（减半，下限 1）并冷却 5s，期间 `acquire` 返回 `reason=cooldown` 和 `wait`。

## 配额口径

- 每站点一个令牌桶：容量 64，每秒回补 1。
- `acquire` 成功 = 占 1 路并发租约 + 扣 1 配额；配额不足不抛错，返回 `reason=quota`、可等待时间 `wait` 和排队位置 `queue`。
- 并发租约 30s，慢站超时占用被回收（日志 `reclaim`），让出并发额度；`report` 只记录结果，不释放租约。

## 输出与日志

`acquire`/`report` 返回 `phase`、`concurrency`、`limit`、`quota`、`backoff`（最近回退记录）；`ramp`/`backoff`/`queue`/`reclaim`/`slow-rtt` 都写入 `logging` 的 `crawlcap` logger，可回溯。同一站点的阶段状态跨任务共享。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
