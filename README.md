# retrywindow

现状：失败后立即重试，最多三次，各任务独立。

问题：站点压力叠加，没有时间上限，不区分失败和放弃。

目标：按站点共享窗口，抖动，放弃标记和取消。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

窗口口径：每个站点一条滑动窗口（`WINDOW_SECONDS` 秒），从窗口内首次失败计时；
窗口过期整体滑动、预算归零。重试时间 = 指数退避（`BASE_DELAY` 翻倍、封顶窗口长度）
+ 窗口内随机抖动（`JITTER_MAX` 秒内），避免多任务同时打到刚恢复的站点。

共享口径：同一站点的所有任务共用窗口和预算（`SITE_BUDGET` 次/窗口），预算耗尽
顺延到下一窗口起点；单任务超过 `MAX_DURATION` 或 `MAX_ATTEMPTS` 即放弃并记录原因。
`due(now)` 出待重试，`windows()` 出共享窗口，`abandoned()` 出放弃列表；
`cancel(task)` 后不再触发，重复排期、取消未知任务等抛 `RetryWindowError`。
