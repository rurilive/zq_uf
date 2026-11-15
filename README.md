# metricroll3

按事件时间的指标上卷：分窗、迟到重算、可合并分位、过期清理。

- `RollStore(window_seconds, ttl_seconds, alpha)`，默认实例见模块级 `add`/`add_batch`/`query`/`drilldown`。
- 事件按 `event_time` 落到对齐窗口；跨窗批次自动拆分，批内乱序不算迟到。
- 计数/求和直接相加；分位用 DDSketch 先合并分布再取值，误差上界 `alpha`，合并与顺序无关。
- 水位线越过窗口结束即视为迟到：只重算该窗口并标记 `recomputed`；早于 `watermark - ttl` 的窗口自动清理。
- `query` 返回窗口起止、`count`/`sum`、分位值、误差区间 `[low, high]`、`recomputed`/`expired`；区间查询的 `windows` 逐窗口结果合并后与上卷一致。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
