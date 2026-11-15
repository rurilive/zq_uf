# tablelease

数仓表写入的跨进程租约模块，纯 Python 标准库（fcntl.flock + JSON 文件），POSIX 单机使用。

## 租约口径

- 状态存于租约目录（环境变量 `TABLELEASE_DIR`，默认 `<tmp>/tablelease`）：每表一个 `<table>.json`（状态）+ `<table>.lock`（flock 互斥），抢占审计追加到 `audit.log`。
- 每次操作在表级 flock 内完成读-改-写，跨进程原子；`acquire(table, owner, ttl)` 成功后持有者为 `owner`，`expires_at = 获取时刻 + ttl`，重入计数 `count = 1`。
- 同一张表同一时刻只有一个写者。未拿到的请求按到达顺序进入 FIFO 等待队列并阻塞轮询，队首且租约空闲时才发放。
- 持有者租约过期（`now >= expires_at`）或持有者进程已退出（pid 检测）时，租约可被抢占；抢占写 `audit.log`（prev_owner、reason: `expired` / `holder_dead`）。

## 重入口径

- 同一 `owner` 再次 `acquire` 即重入：`count += 1`，并在同一 flock 内原子续约（`expires_at` 顺延一个 ttl）。
- `release(table, owner)` 只减一次计数，归零才真正释放；嵌套写入必须成对释放。
- 释放幂等：未持有/已释放时返回 `duplicate=True`，不改状态；非持有者释放返回 `released=False` 且状态不变，失败可安全重试。

## 查看与审计

```python
from tablelease import status, audit_log
status("orders")   # 持有者、剩余 ttl、重入计数、等待队列（任意进程可读）
audit_log()        # 抢占审计
# 或命令行：python3 -m tablelease [table]
```

## 运行

```bash
python3 demo.py                          # 模拟入库进程崩溃后被抢占
python3 -m unittest discover -s tests    # 含跨进程互斥/过期抢占/FIFO/重入/幂等
```

注意：`test_second_owner_also_granted`（既有断言，未改）会真实等待 30s ttl 过期后抢占，属预期行为。
