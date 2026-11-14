# accountrefresh

现状：每次取令牌都刷新，没有过期判断和退避。

问题：并发刷新让旧令牌立刻失效，失败把账号锁死，账号之间互相拖累。

目标：按账号单飞，提前刷新，指数退避和账号隔离。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 使用

- `get_token(account)`：令牌新鲜直接复用；进入提前量或已过期才刷新。
- `refresh(account, force=False)`：手动刷新，`force=True` 跳过缓存和退避。
- `status()`：刷新次数、等待复用数、退避状态、隔离账号与调度权重。
- `audit_log()`：成功、失败、复用、退避、隔离、恢复的全量审计。
- `configure(...)`：调整提前量、退避参数、隔离阈值、签发器和时钟。

## 口径

- 提前量：距过期不足 `refresh_ahead` 秒（默认 30）即提前刷新；失败时旧令牌在过期前仍可用。
- 退避：第 n 次连续失败退避 `min(backoff_base * 2 ** (n - 1), backoff_max)` 秒，`next_try` 前不再请求签发方。
- 隔离：连续失败达到 `isolate_threshold` 次（默认 3）隔离该账号，调度权重按 `0.5 ** 失败次数` 降权（下限 0.05），成功后自动恢复。
- 单飞：每个账号一把锁，同一账号同时只有一个刷新在跑，其余线程等待后复用结果；账号之间互不阻塞。

