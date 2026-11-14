# metricbackfill

指标回填与对账模块，纯 Python 标准库。接口：`plan(range_spec)` 切片，`apply(plan)` 执行。

## 切片口径

- 时间：`shard_days` 指定每片天数，把 `[start, end]` 切成连续窗口（支持整数或 `YYYY-MM-DD`）；缺省整段一片。
- 维度：`dims` 里取值为列表的维度做笛卡尔积，每个组合一片，各片独立重算。
- `rows` 按片均分（余数分给前几片），`plan` 返回 `{"range", "shards", "threshold"}`。

## 执行与对账口径

- 每片先撤销目标表同区间旧行再重算写入（撤销重写），重复执行幂等，不累加翻倍。
- 写入前对账：对比本片原值与重算值的行数、值、crc32 校验和，差异按分片输出在 `results`。
- `abs(row_diff)` 或 `abs(value_diff)` 超过 `threshold` 即停批：本片回滚保留原值，已回填分片保留，`stop_reason` 说明原因。
- 回填记录按分片存入 `Ledger`，`ledger.query(start, end)` 按区间查询（相交即命中）。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
