# shardbalance

采集任务的跨机房分片模块，纯 Python 标准库实现。站点通过一致性哈希
（含虚拟节点）分配到执行机，扩缩容时只迁移归属发生变化的站点，再平衡
先等迁移站点的在跑任务收尾，再原子切换分配表。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 虚拟节点口径

- 每台机器在环上注册 `VIRTUAL_NODES`（默认 128）个虚拟节点，键为
  `机器名#序号`，用 md5 前 4 字节映射到 32 位环空间。
- 站点按自身 md5 哈希在环上顺时针找到第一个虚拟节点，其所属机器即
  归属机器；增删机器只会改变环上相邻区间，未命中区间的站点不动。
- 虚拟节点把机器打散到环上，避免机器数少时负载倾斜；数量固定后
  不要随意调整，否则等价于整表重哈希。

## 再平衡口径

- `assign(site, workers=None)`：不传 `workers` 返回当前分配表中的
  归属；传入机器列表时只做一次一致性哈希计算，不改写分配表。
- `rebalance(workers=None, timeout=None)`：按新机器集合重算环，
  对比当前表得到迁移清单 `(site, 旧机器, 新机器)`，只等待清单内
  站点的在跑任务（`acquire` 登记、`release` 收尾），全部收尾后才
  切换分配表；不受影响的站点任务继续跑，不会被打断。
- 返回 `RebalanceResult`：本身是新分配表（dict），另含 `migrations`、
  `affected_sites` 和 `pending_tasks`；`timeout` 超时抛
  `DrainTimeoutError`，携带未收尾任务且分配表保持不变，可重试。
- 同一站点同一时刻只允许归属机器 `acquire` 一次，重复执行抛
  `DuplicateExecutionError`；空机器列表、重复机器、非法站点名、
  负数超时等参数错误一律抛 `ValueError`/`TypeError`，不静默跳过。
