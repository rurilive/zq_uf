# sampleci

可复现的分层采样与误差区间：固定种子、按字段分层，比例与固定条数可混用，同计划两次结果逐行一致并可重放。

- `sample(rows, plan)` / `replay(rows, plan, recorded)`：分层采样与同计划重放；长尾层（稀有小层/高值层）自动普查，不丢点。
- `summarize(...)`：输出分层、样本量、均值（分层 t 置信区间）与分位数（加权/等权双口径 + 分层 bootstrap 区间）；样本量不足时区间为 `None` 并写明原因，不给误导性误差；长尾层单独统计。
- `full_scan(...)` + `compare(...)`：先在一个指标上对全量真值，均值区间覆盖且偏差达标才推广（分位默认只提示）。
- `write_files(...)`：导出 JSON / 样本明细 CSV / 统计报告 CSV。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
