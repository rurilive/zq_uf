# qualitytrend

质量趋势与阈值模块，纯 Python 标准库，接口 `check(dataset, metric)` / `trend(dataset)`。

口径：
- 基线 = 最近窗口（`window`，默认 7）+ 同周期窗口（`seasonal_window`，按星期对齐）的均值/标准差，均可 `configure()` 或按次 `config=` 配置；
- 基线样本不足 `min_samples` 时回退固定阈值 `fallback_threshold`（0.05）；
- 偏离倍数 = |value − 基线均值| / 基线标准差，≥ `deviation_multiple`（默认 3）才告警；
- 偏离在 [`drift_multiple`, `deviation_multiple`) 且连续 `drift_windows` 个窗口，确认缓慢漂移；
- 告警带基线、偏离、样本和历史曲线片段，按 (dataset, metric) 同原因去重；误报用 `feedback()` 标记；
- `backtest()` 回放历史输出告警数与命中率，`export()` 导出趋势/序列/回测 JSON。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
