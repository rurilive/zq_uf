# metricdedup2

指标去重与版本模块，纯标准库。接口保持 `put(metric)` 和 `flush()`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 版本口径

同键比较键是 `(event_time, version)`，**事件时间优先**：event_time 大者视为更新；
event_time 相同再比较 version（数字与字符串都支持）。没有 `event_time` 的记录排在
带时间戳的记录之后，彼此只按 version 比较，也不参与窗口清理。

- 新记录比较键更大 → 接受并替换（`accepted`）。
- 比较键相等 → 重复消息，幂等处理，不写状态，计 `duplicates`。
- 比较键更小 → 迟到旧版本，**只计数不覆盖**，计 `dropped`。
- 事件时间低于窗口下界（`watermark - window`）→ 直接丢弃，计 `too_late`。

## 窗口口径

水位（watermark）是已见最大 `event_time`，**只增不减**。窗口保留
`event_time >= watermark - window`（边界取等号保留），越界的记录在推进水位时清理并
计 `expired`。窗口长度可配置：`configure(window=…)`；`flush(watermark=…)` 可强制
推进水位（不允许回退）。

## 落盘口径

增量 + 全量结合：每条写入同时追加 `journal.jsonl`（带自增 `seq`），`flush()` 写
`snapshot.json` 全量快照并清空日志。恢复时先读快照、再重放 `seq > snapshot.seq`
的日志，因此重复重放幂等；恢复后从快照里的水位继续推进。

```python
from metricdedup2 import configure, flush, put, reset, stats
configure(state_path="/var/lib/metricdedup2", window=60.0)  # 启用落盘与窗口
put({"key": "pv", "version": 3, "event_time": 1700000000.0, "value": 10})
put({"key": "pv", "version": 2, "event_time": 1699999999.0})   # 旧版本，只计数
flush()                                                        # 写快照、清日志
stats()  # 按字段输出：size/watermark/window/accepted/dropped/duplicates/expired/too_late
```

`configure(state_path=…)` 在路径变化时会从磁盘恢复状态，等价于进程重启；
`reset()` 清空内存并（若启用落盘）写空快照。环境变量
`METRICDEDUP2_STATE_PATH`、`METRICDEDUP2_WINDOW` 可做初始配置，显式 `configure` 优先。
