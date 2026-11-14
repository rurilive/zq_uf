# wmsync

多源水位对齐模块，纯 Python 标准库。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 水位口径

- `update(source, ts)` 上报事件时间；`watermark()` = 各源水位最小值，按最慢源对齐。
- 各源水位单调不减；新源校准到不低于当前全局水位，全局水位整体单调。
- `ts` 落后于当前全局水位即迟到，进侧输出 `late_events()`，不回退水位。

## 空闲口径

- `heartbeat(now, idle_timeout)`：`now - 最近事件时间 >= idle_timeout` 的源标记为空闲。
- 空闲源水位按心跳推进到 `now - idle_timeout`，不再卡住全局最小值。
- 空闲源恢复更新后自动回到活动集合，重新参与最小水位计算。

## 状态与恢复

- `status()` 输出各源水位、全局水位、空闲源和迟到数。
- `snapshot()` 返回可 JSON 序列化状态，`restore(snap)` 用于重启恢复。
