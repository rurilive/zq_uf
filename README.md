# accountrefresh

纯标准库的账号登录态刷新模块：按账号单飞、提前刷新、指数退避、失败隔离与审计。
对外接口保持 `get_token(account)` 与 `refresh(account, force=False)`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 提前量口径

- 后端返回 `expires_in`（秒）即视为带过期时间，过期时刻记为 `expires_at`。
- `lead_time` 默认 30 秒：当 `now >= expires_at - lead_time` 时，`get_token` 才触发刷新；窗口内直接复用缓存令牌。
- 后端不返回 `expires_in` 时视为无过期信息，行为与旧版一致，每次取令牌都刷新。
- `refresh(account, force=True)` 绕过缓存与退避判断强制刷新，但仍参与单飞。

## 退避与隔离口径

- 刷新失败按指数退避：`next_try = now + min(backoff_cap, backoff_base * backoff_factor ** (failures-1))`，默认 1s、2 倍、上限 60s。
- 退避窗口内的普通刷新直接短路（结果带 `backoff=True`、`next_try`），不调用后端；有旧令牌时通过 `token`/`stale` 返回。
- 连续失败达到 `isolate_threshold`（默认 3）后账号隔离，调度权重从 1.0 降到 0.1，可经 `weight(account)` 或 `report()` 读取。
- 账号各自维护独立状态与锁，互不阻塞；一次成功即清零失败计数、解除隔离并恢复权重。

## 单飞与审计

- 同账号并发刷新只有一个请求真正执行，其余线程等待并复用同一结果，等待次数计入 `report()` 的 `wait_reuse`。
- 每次 `refresh_start`、`refresh_success`、`refresh_failure`、`backoff_skip`、`wait_reuse` 都写入审计，经 `audit_records()` 读取，或用 `configure(audit_file=...)` 落 JSONL。
- `report()` 汇总刷新次数 `refresh_counts`、等待复用数 `wait_reuse`、退避状态 `backoff` 与隔离账号 `isolated`；`stats()` 保持旧的每账号刷新次数字典。
