# crontick

采集任务定时触发：多实例租约互斥、启动错峰、错过补偿、任务级时区。
接口保持 `tick(now, run)` 和 `next_run(task, now)`；多实例用
`Scheduler(tasks, store=FileStore(共享目录), instance_id=...)`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 错峰窗口

- 每任务配 `jitter_window` 秒，实例偏移 = sha256(实例:任务:slot) % window，落在 [slot, slot+window)。
- 同一实例同一 slot 偏移确定，`next_run` 与 `tick` 口径一致；窗口为 0 即不错峰。
- 未到偏移时刻，`tick` 返回 `reason="jitter_wait"`，不抢租约、不触发。

## 补偿与互斥口径

- 恢复后只补最近一次应触发的 slot，一次；更早错过的计入 `missed_slots`，不逐个补。
- 补跑结果 `catchup=True`、`reason="catchup"`；已触发的 slot 再 tick 返回 `already_fired`。
- 触发前抢租约（FileStore 用 O_EXCL 原子创建，TTL 默认等于 interval），抢不到返回 `lease_held`。
- slot 按任务 `tz`（IANA 名）的本地零点对齐；naive 时间按 UTC 处理；配置/回调异常抛 ScheduleError/CronTickError。
