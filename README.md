# downsample

时序数据降采样：按时间窗口聚合，纯 Python 标准库。

## 接口

`resample(points, rule, aggs=None)`，points 为 `{"ts": epoch秒, "v": 值}`。

- rule 为整数：旧的等间隔抽点（兼容保留）。
- rule 为字符串或 dict：窗口聚合。固定时长 `"300s"/"5m"/"1h"/"1d"`（按 epoch 对齐，可用 `origin` 改原点）；日历粒度 `"1day"/"1week"/"1month"`（UTC 边界，周从周一开始）。
- dict 可配：`fill`（`"skip"` 跳过 / `"pad"` 补空窗口）、`duplicate`（重复时间戳 `"overwrite"` 覆盖 / `"accumulate"` 累加）。
- `aggs` 从 `min/max/mean/last` 中选，默认全量；mean 为窗口内算术平均，last 为窗口内最大时间戳的值。

## 输出

每个窗口：`start`/`end`（左闭右开）、`count`（样本数）、`gap`（是否空窗口）、`gap_len`（所在连续缺口长度，非空窗口为 0）及聚合值；乱序点自动归入对应窗口。
`rollup(windows, rule)` 用上一级窗口继续聚合更粗粒度，结果与直接对原始数据降采样一致（上一级需保留默认全量聚合）。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
