# crawllease

采集任务的跨进程租约：纯标准库，状态存 JSON 文件，读写都在 `fcntl.flock`
排他锁内完成，多进程下同一任务同时只有一个有效租约。

## 租约与栅栏口径

- `claim(task_id, owner, ttl)`：领取租约，栅栏值（fence）单调递增并随结果返回；`ttl=0` 表示不过期。
- 新持有者领到更高栅栏值后，旧持有者的 `renew`/`release` 一律返回 `False`（写入被拒）。
- 租约过期后可被重新领取；持有者更替会记录一条回收记录（`reason` 为 `expired` 或 `takeover`），`reclaims()` 可查。
- `renew`/`release` 在文件锁内完成 读-改-写，崩溃进程留下的租约到期自动可回收。
- `status(task_id)` 返回当前持有者与栅栏值；参数非法抛 `ValueError`，状态文件损坏抛 `LeaseError`。
- 状态文件路径用环境变量 `CRAWLLEASE_STATE` 指定，默认在系统临时目录。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
