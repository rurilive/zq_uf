# quotasched

现状：任务按提交顺序出队，CPU 和内存需求只存在字段里。

问题：大任务独占机器，小任务等几小时，紧急任务没法插队。

目标：按资源装箱调度，高优先级可抢占并保存进度，输出利用率。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 任务字段与参数

- `id`（必填，唯一）、`cpu`（默认 1，>0）、`mem`（默认 0，>=0）、`priority`（默认 0，整数，越大越优先）、`progress`（0~1，断点续跑用）。
- 队列容量：`Queue(cpu=64, mem=256)`，可用 `clock` 注入时钟便于测试复现。
- 非法任务（缺 id、重复 id、资源超容量、类型错误）直接抛 `ValueError`/`TypeError`，不静默跳过。

## 调度与抢占规则

- `next()` 按（优先级降序、入队先后）扫描，派出第一个能装进剩余容量的任务；都装不下返回 `None`。
- 队首装不下时，抢占优先级严格更低的运行任务（先抢优先级最低、进度最小的）；全部抢占仍装不下则不抢占。
- 被抢占任务保留 `progress` 重回等待队列，再次派出时结果带 `progress` 字段续跑；运行中用 `report(id, progress)` 上报进度，`done(id)` 释放资源。
- `utilization()` 返回 cpu/mem 利用率，`stats()` 输出各任务等待时长、平均/最长等待与抢占次数。
- `snapshot()`/`restore(data)` 导出恢复 JSON 可序列化状态，重启后可复现。
