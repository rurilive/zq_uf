# batchwindow

纯标准库的事件时间滚动窗口：按事件时间落窗，迟到数据触发受影响窗口撤销重算，重算幂等，元数据与审计记录落盘对账。

## 窗口口径

- 按 `event["ts"]` 落窗：`window_id = floor(ts / window_size)`，默认窗口 86400 秒（一天）。
- 窗口元数据以闭区间 `[start, end]` 记录，`end = start + window_size`；边界点归起始于该点的右侧窗口，相邻窗口不重复收数。
- 水位线 `watermark = max(已观察 ts) − allowed_lateness`（默认宽限 0）；水位线严格越过窗口末端（`end < watermark`）后到达的事件判为迟到，保证闭区间末端点本身仍开放。
- 事件可带 `id` 去重，`value` 参与求和；聚合值为 `{"count", "sum"}`，只从窗口内原始事件全量派生。
- 接口保持 `assign(event)` 落窗（返回含 `window/count/sum/late/watermark`），`recompute(window)` 重算单窗。

## 迟到与重算口径

- 迟到事件照常入窗，并自动触发该窗口重算；迟到事实写入 `late_events.json`。
- 重算顺序固定为：撤销旧聚合值 → 从原始事件全量重算 → 写入新值，审计记录保留 `old_value/new_value/reason/trigger_event`。
- 幂等：聚合不做增量累加，重复 `recompute` 结果一致（之后 `old_value == new_value`）；带 `id` 的重复事件不二次入窗。
- 查询：`get_window(w)` / `windows()` 看窗口元数据，`late_events(w)` 看迟到，`recompute_records(window=, start_window=, end_window=)` 按窗口或区间查重算记录。
- 落盘（原子替换）：`events.json`、`windows.json`、`late_events.json`、`recompute_records.json`、`meta.json`；`configure()` 可换目录并恢复状态，`reset()` 清空。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
