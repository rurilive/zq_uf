# metricrollup2

按事件时间上卷：`add(metric)` 带 `ts` 时按 `window_size` 落窗（无 `ts` 用 `window` 标签），批次跨窗自动切分。
迟到事件（`ts` 小于维度水位线）触发所在窗口重算；重算重放窗口内事件，幂等；同 `id` 事件去重。
计数/求和可加；分位数用可合并、可序列化的加权草图（`configure(sketch_capacity=...)`，误差随容量收敛）。
`query(dim, window)` 返回窗口边界、重算次数与草图；`dim="*"` 为上卷，与下钻求和一致。
窗口保留期：`configure(retention_windows=N)`，过期窗口自动清理；维度基数变化时重建索引，`stats()` 可查。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
