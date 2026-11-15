# metricmerge

按事件时间落窗的指标合并：迟到触发整窗重算，分位数用草图合并。

口径：
- 窗口：`event_time` 按 `WINDOW_SIZE_SECONDS`（默认 1 天，UTC）对齐出窗口边界；显式 `window` 字段优先。
- 迟到：`event_time` 早于当前水位线（见过的最大事件时间）即迟到，触发整窗重算。
- 重算：按窗口主键定位，先撤销旧版本再从窗口事件重放，新版本覆盖旧版本（不累加）；计数/求和可加，分位数走草图。
- 保留期：超过 `RETENTION_SECONDS`（默认 7 天）的迟到数据进 `backfill_queue()` 补算队列，不落窗。
- 幂等：带 `id` 的事件按窗口去重，重复迟到不重复累加；窗口干净时 `recompute` 返回 `recomputed=False`。
- 草图：加权样本 + 确定性压缩，状态可 JSON 序列化；`merge_sketches` 合并并做基数对账（事件数 = 计数 = 草图权重）。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
