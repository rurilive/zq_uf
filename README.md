# metricdedup

带窗口的幂等去重模块，纯 Python 标准库。接口：`put(metric)` / `flush()`，`metric = {"name": ..., "ts": ...}`。

## 去重口径
- 按指标键（name）去重：同键且 `ts <= 已存最新 ts` 判为重复，只计 duplicate，不写入。
- 同键保留最新时间戳：乱序到达的旧值直接丢弃，不覆盖新值。
- 窗口按事件时间滑动：水位 = 状态中最新 ts；`ts <= 水位 - window` 的迟到数据计 expired，不写入。

## 状态清理与上限
- 每次写入后清理窗口外状态，过期键按最旧优先淘汰，数量计入 expired。
- 状态条目上限 `max_size`（默认 10000），超出同样按最旧优先淘汰。
- 状态每次变更后原子落盘（默认 `metricdedup_state.json`），重启后模块加载时自动恢复。
- `dedup.configure(window_seconds=, max_size=, state_path=)` 可调参数，`state_path=None` 关闭持久化。

## 输出
`flush()` → `{"written", "duplicate", "expired", "size"}`：写入数、重复数（单独计数）、过期/淘汰数、当前状态大小。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
