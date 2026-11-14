# partitionlease

带栅栏的分区租约，纯 Python 标准库。同一分区同一时刻只有一个有效租约。

- `acquire(dataset, partition, owner, ttl)`：拿租约，返回持有者与单调递增的 `fence_token`；挤掉他人有效租约记 `preempt`，接收过期租约记 `reclaim`。
- `renew(...)` / `release(...)`：原子操作，只有当前持有者能续期或释放，返回是否成功；过期未续的租约可被他人回收。
- `write(dataset, partition, owner, fence_token, payload)`：写入必须带当前栅栏值；旧栅栏抛 `StaleFenceError`，过期抛 `LeaseExpiredError`，非持有者抛 `NotLeaseHolderError`。
- `holder(...)` 查当前有效持有者，`events(...)` 输出持有者、栅栏、抢占、回收、续期、释放记录。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
