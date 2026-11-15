# dataprof

现状：所有行留在内存，画像时对全量数据重算。

问题：数据量一大就爆内存，异常阈值写死，画像不随数据版本更新。

目标：按批增量更新，分位数用草图，异常按分位和倍数判定。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

口径：
- 画像按批增量更新：均值用 Welford，分位数用可合并草图（容量 128），空值率 = 空值行 / 累计行，按批累计。
- 异常：样本 ≥4 且 IQR>0 时按 [q1-1.5·IQR, q3+1.5·IQR] 判定，否则退回绝对阈值 100（单侧上限，兼容旧行为）。
- 阈值可按字段覆盖：configure(field_thresholds={"f": {"multiplier": 3.0, "absolute_threshold": 50.0}})。
- 异常样本保留原值（最多 100 条）；字段类型变化时该字段统计重置。
- 每次 observe 递增数据版本；configure(storage_dir=...) 后画像落盘（latest.json + profile_vN.json），历史保留最近 5 版；export_profile() 可导出。
