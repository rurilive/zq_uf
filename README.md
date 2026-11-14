# qualityscore

现状：规则等权，按失败数折算分数。

问题：权重不分，趋势看不出，阈值写死。

目标：加权评分，趋势环比，连续下降告警。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

权重口径：每条规则带 `weight`（默认 1）和 `level`（critical/major/minor，仅标记）；
规则分 = 1 - failed_rows/total_rows（只给 `failed` 布尔时按 0/1 行折算）；
总分 = Σ(weight × 规则分) / Σ(weight)，保留两位小数；分项和权重写入报告 `breakdown`。

趋势口径：按天留档（`date`，同日覆盖），`trend(dataset)` 返回最近 `window` 天总分，
`detail=True` 带日期、环比 delta 和分项可回看；环比下降时报告给 `decline_drivers` 下钻。
`configure(threshold, decline_days, window, history_file)` 可配：总分低于阈值告警，
连续 `decline_days` 天下降才告警；`history_file` 指向 JSON 文件即持久化留档。
