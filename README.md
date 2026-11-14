# cdcsnapshot

变更流与全量快照的位点对齐模块（纯 Python 标准库）。快照记录起始位点，快照期间的变更先缓存、提交后按序补应用；位点单调推进、重放幂等；用行数与校验和做一致性校验并输出差异行。

## 位点口径

- 事件带整数 `pos`（位点），快照和变更流共用同一个位点文件（默认 `cdcsnapshot_position.json`，原子写入），恢复后从最后确认位点继续。
- `pos <= 确认位点` 的事件视为重放直接跳过，位点只增不减；不带 `pos` 的事件按旧行为直接应用、不推进位点。
- 位点仅在真正推进或快照窗口开关时原子落盘；位点未变化不产生写操作，无位点文件且位点为 0 时不创建文件。
- `begin_snapshot(table)` 记录起始位点（位点文件同时写入快照标记），窗口内 `apply` 只进缓存；`commit_snapshot()` 补应用缓存并把位点推进到最后一个事件。

## 一致性口径

- 校验对比**行数**与 **SHA-256 校验和**：按 key 排序后对 `[key, row]` 的稳定 JSON 逐行摘要。
- 校验失败时 `diff` 列出每个差异行的 `key`、`expected`、`actual`（缺失侧为 `null`）。

## 接口

```python
from cdcsnapshot import SnapshotStore, check_consistency

store = SnapshotStore("cdc_position.json")

store.begin_snapshot("orders")          # 记录起始位点，打开缓存窗口
store.apply_events(events)              # 窗口内只缓存；窗口外直接应用
snap = store.commit_snapshot()          # 补应用缓存，输出位点/缓存/校验结果

store.check_consistency("orders", snap) # 行数 + 校验和，失败带 diff 行
```

事件格式：`{"key": "a", "row": {"v": 1}, "pos": 3, "op": "upsert"}`，`op` 默认 `upsert`，可选 `delete`。模块级 `snapshot(table)` 与 `apply(events)` 保留原有签名，`snapshot` 为无并发写入时的 begin + commit 快捷封装。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
