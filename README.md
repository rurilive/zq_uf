# changebatch

变更批次整批原子推进：失败按写入逆序回滚，重复提交按位点跳过，冲突保留两侧。

- `apply(batch)`：`{"id", "seq", "rows": [{"key", "value"}]}`。整批成功才推进
  水线；失败整批回滚且不推进；同 id 或 `seq <= 水线` 判为重复提交整批跳过；
  越位批次（`seq > 水线 + 1`）进入 pending 阻塞，不留数据缺口。
- `resume(token)`：从最后成功批继续，按 seq 顺序重试 pending；再次失败即停。
- `snapshot()` / `watermark()` / `pending()` / `journal()` / `export_rollbacks(path)`：
  对账、位点、待恢复批、审计日志、回滚明细导出。
- 返回统一包含 `applied` / `rolled_back` / `skipped` / `conflicts`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
