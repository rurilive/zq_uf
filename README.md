# tablelock

跨进程租约锁：锁状态（持有者、过期时间、重入计数、等待队列、抢占记录）
落在按表名分片的锁文件里（`$TABLELOCK_DIR/<sha1前2位>/<表名>.lock`，默认
`$TMPDIR/tablelock`），读写全程 `fcntl.flock` 互斥，纯标准库。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 租约口径

- `acquire(table, owner, ttl)`：同一表同一时刻只有一个写者；锁空闲或
  已过期（`expires_at <= now`）即获得，过期获得记一条抢占记录。
- 持有者未过期时他人 acquire 返回 `ok: False`，并按到达顺序进等待队列。
- 重入（同一 owner 再 acquire）在同一把文件锁内原子续期 `now + ttl`。
- `status(table)` 输出持有者、等待队列（排队顺序）和全部抢占记录。

## 重入口径

- 同一 owner 重复 acquire 只加重入计数 `count`，不构成死锁。
- `release(table, owner)` 每调一次 `count -1`，归零才真正释放。
- 释放幂等：未持有、已释放、已被抢占的 owner 调 release 返回 False，
  不报错、不影响当前持有者；进程崩溃不释放，靠租约过期被抢占恢复。
