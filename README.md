# accountrefresh

现状：每次取令牌都刷新，没有过期判断和退避。

问题：并发刷新让旧令牌立刻失效，失败把账号锁死，账号之间互相拖累。

目标：按账号单飞，提前刷新，指数退避和账号隔离。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 提前量

- 令牌源返回 `expires_in`，模块缓存到 `expires_at`。
- 剩余有效期 > `REFRESH_AHEAD`（默认 30s）直接复用缓存，否则刷新。
- 默认 `TOKEN_TTL=0`（立即过期），兼容旧的“每次取都刷”行为。
- `refresh(account, force=True)` 跳过缓存判断强制刷新。
- 同一账号单飞：并发请求只有一个在刷，其余等待复用结果。

## 退避与隔离

- 第 n 次连续失败退避 `min(BACKOFF_BASE * 2^(n-1), BACKOFF_MAX)` 秒。
- 退避期内 `get_token`/`refresh` 抛 `BackoffError`，带 `next_try`，不再请求源。
- 连续失败 >= `ISOLATE_THRESHOLD`（默认 3）账号被隔离，调度权重降为 0.1。
- 退避、锁、隔离都按账号独立，账号之间互不阻塞。
- 成功/失败/隔离均写审计，`audit_log()` 可查；`stats()` 输出刷新次数、
  等待复用数、退避状态、隔离账号和调度权重。
