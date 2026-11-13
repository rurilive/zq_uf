# driftcheck

现状：单个指标和写死的10%阈值比较，一天一次。

问题：业务量变化后误报，缓慢漂移看不出，周末和促销日天天告警。

目标：历史窗口和同周期基线，支持同比环比、趋势判断和告警合并。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 基线口径

- 连续窗口基线：最近 `window` 天（默认 7）的均值和总体标准差。
- 同比：按 `period`（默认 7 天）对齐取最近 `period_window` 个同周期样本的均值。
- 环比：当前值对前一天值的相对变化。
- 趋势：同周期对齐样本（含当前值）做最小二乘拟合，斜率折算成每天相对变化。
- 无 history 时退化为旧的固定阈值口径，结果字段保持兼容。

## 阈值

- `multiplier`（默认 3.0）：|z 分数| 超过该倍数才告警，窗口和同周期各算一次。
- `min_ratio`（默认 0.05）：标准差为 0 时按该相对刻度折算 z。
- `trend_slope`（默认 0.02）：归一化日斜率超过即判缓慢漂移。
- 告警带 value、baseline、deviation、zscore、window、wow、yoy、reasons。
- `merge_alerts` 合并同数据集同原因的连续告警；`backtest` 用历史序列回放阈值效果。
