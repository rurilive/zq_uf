# alertmerge

数据任务告警聚合：按根因和任务组去重，窗口内只发一条，恢复时发汇总。

问题：上游一失败下游几百条同时响，同一根因重复，恢复通知对不上。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 聚合键与窗口

- 聚合键为 `(reason, group)`，缺省为 `("unknown", "default")`；`job` 必填。
- `configure(window_seconds=N)` 开启聚合，窗口为 `[首条时间, 首条时间+N)`。
- 窗口内首条 firing 立即发送，后续同键告警只更新统计不再发送。
- 窗口外新告警开启新窗口并再次通知；`window_seconds=0`（默认）逐条透传。
- 发送行带 `reason/group/state/jobs/first/last`：影响任务数与首末时间。
- recovered 关闭事件并发一条汇总（含完整影响统计），窗口内重复恢复合并。
- 无对应 firing 的 recovered 抛 `ValueError`，不静默跳过。

## 参数与阈值

- `window_seconds`：聚合窗口秒数，非负数字，默认 0（不聚合）。
- `escalate_after`：同窗口合并条数达到该值时发一次 `escalated`，默认 None。
- `silence(reason, group, until)`：静默到 epoch 秒 `until`，期间 firing 不发送仍统计。
- 静默不抑制恢复汇总；`emit` 缺省 `state="firing"`、`time=time.time()`。
- 非法告警（缺 job、未知 state、非数字 time）抛 `ValueError`。
- `dump_state()`/`load_state()` 导出/恢复 JSON 可序列化状态，便于重启复现。
