# qualityscore

现状：规则等权，按失败数折算分数。

问题：权重不分，趋势看不出，阈值写死。

目标：加权评分，趋势环比，连续下降告警。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 权重口径

- 每条规则带 `weight`（默认 1.0）和 `level`（critical/major/minor，仅标记不参与计算）。
- 单规则得分 = 1 - 失败行数 / 总行数（行数取规则的 `rows`，缺省用批次的 `rows`）。
- `failed` 传布尔时兼容旧口径：失败即 0 分。总分 = Σ(weight × 规则分) / Σ(weight)，保留两位小数。
- 分项 `breakdown` 含权重、失败行数、得分和贡献占比（贡献之和 = 总分）。

## 趋势口径

- 按天保留总分与分项，同日重跑覆盖，`trend(dataset, days=N)` 回看最近 N 天总分。
- 环比 `delta` = 当日总分 - 前一日总分；趋势按最近 `window` 天窗口计算。
- `drivers` 列出较前日得分下降的规则，可下钻定位下降原因。
- 告警：`threshold` 触发低于阈值告警；连续 `decline_days` 天下降才触发连续下降告警。
- 三项均可用 `configure(threshold=, decline_days=, window=)` 配置；`report(dataset)` 输出完整留档（每日分项、趋势、告警、配置），demo 写入 `reports/`。
